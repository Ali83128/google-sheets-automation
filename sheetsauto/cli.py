"""Command line entry point: python -m sheetsauto {sync,clean,report,all}."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .backend import FakeBackend, GspreadBackend
from .config import load_config
from .jobs import make_writer, run_clean, run_report, run_sync
from .report import FakeMailer, SmtpMailer

log = logging.getLogger("sheetsauto")


def build_backend(cfg, args):
    kind = args.backend or cfg.backend
    if kind == "fake":
        return FakeBackend.load(args.fake_store or cfg.fake_store)
    if kind == "gspread":
        return GspreadBackend(cfg.credentials_file, cfg.spreadsheet_key)
    raise SystemExit(f"unknown backend: {kind}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sheetsauto", description="Google Sheets automation jobs")
    p.add_argument("job", choices=["sync", "clean", "report", "all"])
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--backend", choices=["fake", "gspread"], help="override config backend")
    p.add_argument("--fake-store", help="JSON file used by the fake backend")
    p.add_argument("--email", action="store_true", help="email the weekly report (report/all)")
    p.add_argument("--dry-email", action="store_true", help="print the email instead of sending it")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
    cfg = load_config(args.config)
    base = Path(args.config).resolve().parent if Path(args.config).exists() else Path(".")
    backend = build_backend(cfg, args)
    writer = make_writer(backend, cfg, log=log.warning)
    log.info("backend=%s quota: %s ranges/request, %s requests/min",
             args.backend or cfg.backend, cfg.quota["max_ranges_per_request"], cfg.quota["requests_per_minute"])

    if args.job in ("sync", "all"):
        r = run_sync(backend, cfg, writer, base)
        log.info("sync    source=%s inserted=%d updated=%d unchanged=%d duplicate_ids_merged=%d api_requests=%d",
                 cfg.source.get("path"), r.inserted, r.updated, r.unchanged, r.duplicate_ids, r.requests)
    if args.job in ("clean", "all"):
        c = run_clean(backend, cfg, writer)
        log.info("clean   clean_rows=%d review_rows=%d exact_duplicates_removed=%d",
                 len(c.clean), len(c.review), c.duplicates_removed)
    if args.job in ("report", "all"):
        mailer = None
        if args.dry_email:
            mailer = FakeMailer()
        elif args.email:
            mailer = SmtpMailer.from_config(cfg.smtp, cfg.report.get("email_from", ""))
        rep = run_report(backend, cfg, writer, mailer)
        log.info("report  week=%s revenue=%.2f (prev %.2f, %s) orders=%d",
                 rep.week_start, rep.revenue, rep.prev_revenue,
                 rep.change(rep.revenue, rep.prev_revenue), rep.orders)
        if isinstance(mailer, FakeMailer) and mailer.sent:
            to, subject, _ = mailer.sent[0]
            log.info("email   (dry run) to=%s subject=%r", ",".join(to), subject)
    log.info("done    total_api_requests=%d", writer.requests_sent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
