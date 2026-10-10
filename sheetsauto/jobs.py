"""Job runners shared by the CLI and tests."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .backend import RangeUpdate, SheetsBackend
from .clean import CleanResult, clean_rows
from .config import Config
from .quota import BatchWriter
from .report import Mailer, WeeklyReport, build_report
from .sync import SyncResult, load_source, upsert


def make_writer(backend: SheetsBackend, cfg: Config, log=None, **overrides) -> BatchWriter:
    q = dict(cfg.quota)
    q.update(overrides)
    return BatchWriter(
        backend,
        max_ranges_per_request=int(q["max_ranges_per_request"]),
        requests_per_minute=int(q["requests_per_minute"]),
        max_retries=int(q["max_retries"]),
        base_delay=float(q["base_delay"]),
        log=log,
        **{k: v for k, v in overrides.items() if k in ("sleep", "clock", "jitter")},
    )


def replace_tab(writer: BatchWriter, tab: str, rows: list[list[str]]) -> None:
    backend = writer.backend
    backend.ensure_tab(tab)
    writer.call(lambda: backend.clear(tab))
    if rows:
        writer.add(RangeUpdate(tab, 1, rows))
        writer.flush()


def run_sync(backend: SheetsBackend, cfg: Config, writer: BatchWriter, base: Path = Path(".")) -> SyncResult:
    records = load_source(cfg.source, base)
    return upsert(writer, cfg.tabs["orders"], records, cfg.id_column)


def run_clean(backend: SheetsBackend, cfg: Config, writer: BatchWriter) -> CleanResult:
    raw = writer.call(lambda: backend.get_values(cfg.tabs["orders"]))
    res = clean_rows(raw, id_column=cfg.id_column, default_cc=str(cfg.default_country_code),
                     date_formats=list(cfg.date_formats), canonical_values=cfg.canonical_values)
    replace_tab(writer, cfg.tabs["clean"], [res.header] + res.clean if res.header else [])
    replace_tab(writer, cfg.tabs["review"], [res.header + ["problem"]] + res.review if res.header else [])
    return res


def run_report(backend: SheetsBackend, cfg: Config, writer: BatchWriter, mailer: Mailer | None = None) -> WeeklyReport:
    rows = writer.call(lambda: backend.get_values(cfg.tabs["clean"]))
    if not rows:
        raise ValueError("Clean tab is empty - run the clean job first")
    we = cfg.report.get("week_ending")
    week_ending = date.fromisoformat(str(we)) if we else None
    rep = build_report(rows[0], rows[1:], week_ending)
    replace_tab(writer, cfg.tabs["report"], rep.rows())
    to = cfg.report.get("email_to") or []
    if mailer is not None and to:
        mailer.send(list(to), f"Weekly sales report - week of {rep.week_start.isoformat()}", rep.as_text())
    return rep
