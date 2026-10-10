"""Sync job: pull orders from a CSV export or a REST API and upsert them into a tab."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .backend import RangeUpdate, Row
from .quota import BatchWriter


def load_csv(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def load_api(url_or_path: str, fetch: Callable[[str], str] | None = None) -> list[dict]:
    """Read ``{"orders": [...]}`` from a URL (via ``fetch``) or a local JSON file."""
    if fetch is not None:
        payload = json.loads(fetch(url_or_path))
    else:
        payload = json.loads(Path(url_or_path).read_text(encoding="utf-8"))
    return list(payload["orders"] if isinstance(payload, dict) else payload)


def load_source(source: dict, base: Path = Path(".")) -> list[dict]:
    kind = source.get("type", "csv")
    path = source.get("path") or source.get("url")
    if kind == "csv":
        return load_csv(base / path)
    if kind == "api":
        if str(path).startswith("http"):
            import urllib.request

            def fetch(u: str) -> str:
                with urllib.request.urlopen(u, timeout=30) as r:  # noqa: S310 - configured URL
                    return r.read().decode("utf-8")

            return load_api(path, fetch)
        return load_api(str(base / path))
    raise ValueError(f"unknown source type: {kind}")


@dataclass
class SyncResult:
    inserted: int
    updated: int
    unchanged: int
    duplicate_ids: int
    requests: int


def upsert(
    writer: BatchWriter,
    tab: str,
    records: list[dict],
    id_column: str,
) -> SyncResult:
    """Insert new rows, update changed rows in place, never duplicate an ID."""
    backend = writer.backend
    backend.ensure_tab(tab)
    existing = writer.call(lambda: backend.get_values(tab))
    start_requests = writer.requests_sent - 1

    if existing and existing[0]:
        header = list(existing[0])
    else:
        header = []
    for rec in records:
        for key in rec:
            if key not in header:
                header.append(key)
    if id_column not in header:
        raise ValueError(f"id column '{id_column}' missing from data")
    id_idx = header.index(id_column)

    row_of: dict[str, int] = {}
    current: dict[str, Row] = {}
    for i, row in enumerate(existing[1:], start=2):
        if len(row) > id_idx and row[id_idx]:
            row_of[row[id_idx]] = i
            current[row[id_idx]] = row + [""] * (len(header) - len(row))

    if not existing or existing[0] != header:
        writer.add(RangeUpdate(tab, 1, [header]))

    to_append: list[Row] = []
    seen_new: dict[str, int] = {}
    updated = unchanged = dup_ids = 0
    for rec in records:
        key = str(rec.get(id_column, "")).strip()
        if not key:
            continue
        row = [str(rec.get(col, "") if rec.get(col) is not None else "") for col in header]
        if key in row_of:
            if current[key] != row:
                writer.add(RangeUpdate(tab, row_of[key], [row]))
                current[key] = row
                updated += 1
            else:
                unchanged += 1
        elif key in seen_new:  # same ID twice in one import: keep the last version
            to_append[seen_new[key]] = row
            dup_ids += 1
        else:
            seen_new[key] = len(to_append)
            to_append.append(row)

    writer.flush()
    if to_append:
        writer.call(lambda: backend.append_rows(tab, to_append))
    return SyncResult(
        inserted=len(to_append),
        updated=updated,
        unchanged=unchanged,
        duplicate_ids=dup_ids,
        requests=writer.requests_sent - start_requests,
    )
