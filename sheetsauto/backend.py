"""Spreadsheet backends.

Every job talks to a ``SheetsBackend``. ``GspreadBackend`` is the real Google
Sheets implementation; ``FakeBackend`` keeps tabs in memory (optionally
persisted to a JSON file) so the jobs can be run and tested offline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence

Row = list[str]


class RateLimitError(Exception):
    """Raised when the API answers HTTP 429 (quota exceeded)."""


@dataclass
class RangeUpdate:
    """Overwrite rows starting at 1-based ``start_row`` of ``tab``."""

    tab: str
    start_row: int
    rows: list[Row]


class SheetsBackend(Protocol):
    def ensure_tab(self, tab: str) -> None: ...
    def get_values(self, tab: str) -> list[Row]: ...
    def clear(self, tab: str) -> None: ...
    def batch_update(self, updates: Sequence[RangeUpdate]) -> None: ...
    def append_rows(self, tab: str, rows: Sequence[Row]) -> None: ...


def _as_str(v) -> str:
    return "" if v is None else str(v)


@dataclass
class FakeBackend:
    """In-memory spreadsheet. Counts API-style requests so quota logic is testable."""

    tabs: dict[str, list[Row]] = field(default_factory=dict)
    path: Path | None = None
    requests: int = 0
    fail_next: int = 0  # simulate this many 429 responses before succeeding

    @classmethod
    def load(cls, path: str | Path) -> "FakeBackend":
        p = Path(path)
        tabs = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return cls(tabs=tabs, path=p)

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.tabs, indent=1), encoding="utf-8")

    def _hit(self) -> None:
        self.requests += 1
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RateLimitError("429: Quota exceeded (simulated)")

    def ensure_tab(self, tab: str) -> None:
        self.tabs.setdefault(tab, [])

    def get_values(self, tab: str) -> list[Row]:
        self._hit()
        return [list(r) for r in self.tabs.get(tab, [])]

    def clear(self, tab: str) -> None:
        self._hit()
        self.tabs[tab] = []

    def batch_update(self, updates: Sequence[RangeUpdate]) -> None:
        self._hit()
        for u in updates:
            sheet = self.tabs.setdefault(u.tab, [])
            for i, row in enumerate(u.rows):
                idx = u.start_row - 1 + i
                while len(sheet) <= idx:
                    sheet.append([])
                sheet[idx] = [_as_str(v) for v in row]
        self.save()

    def append_rows(self, tab: str, rows: Sequence[Row]) -> None:
        self._hit()
        self.tabs.setdefault(tab, []).extend([_as_str(v) for v in r] for r in rows)
        self.save()


class GspreadBackend:
    """Google Sheets via a service account (gspread)."""

    def __init__(self, credentials_file: str, spreadsheet_key: str):
        import gspread

        self._gspread = gspread
        client = gspread.service_account(filename=credentials_file)
        self.book = client.open_by_key(spreadsheet_key)

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except self._gspread.exceptions.APIError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 429:
                raise RateLimitError(str(exc)) from exc
            raise

    def _ws(self, tab: str):
        try:
            return self.book.worksheet(tab)
        except self._gspread.exceptions.WorksheetNotFound:
            return self._call(self.book.add_worksheet, title=tab, rows=1000, cols=26)

    def ensure_tab(self, tab: str) -> None:
        self._ws(tab)

    def get_values(self, tab: str) -> list[Row]:
        return self._call(self._ws(tab).get_all_values)

    def clear(self, tab: str) -> None:
        self._call(self._ws(tab).clear)

    def batch_update(self, updates: Sequence[RangeUpdate]) -> None:
        by_tab: dict[str, list[dict]] = {}
        for u in updates:
            by_tab.setdefault(u.tab, []).append(
                {"range": f"A{u.start_row}", "values": [[_as_str(v) for v in r] for r in u.rows]}
            )
        for tab, data in by_tab.items():
            self._call(self._ws(tab).batch_update, data, value_input_option="USER_ENTERED")

    def append_rows(self, tab: str, rows: Sequence[Row]) -> None:
        self._call(
            self._ws(tab).append_rows,
            [[_as_str(v) for v in r] for r in rows],
            value_input_option="USER_ENTERED",
        )
