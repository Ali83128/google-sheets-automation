from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    backend: str = "fake"
    fake_store: str = "output/fake_sheet.json"
    credentials_file: str = "service_account.json"
    spreadsheet_key: str = ""
    tabs: dict = field(
        default_factory=lambda: {
            "orders": "Orders",
            "clean": "Clean",
            "review": "Review",
            "report": "Weekly Report",
        }
    )
    source: dict = field(default_factory=lambda: {"type": "csv", "path": "data/orders_export.csv"})
    id_column: str = "order_id"
    default_country_code: str = "1"
    canonical_values: dict = field(default_factory=dict)
    date_formats: list = field(default_factory=lambda: ["%Y-%m-%d", "%d/%m/%Y", "%b %d %Y", "%Y/%m/%d"])
    quota: dict = field(
        default_factory=lambda: {
            "max_ranges_per_request": 50,
            "requests_per_minute": 50,
            "max_retries": 5,
            "base_delay": 1.0,
        }
    )
    report: dict = field(default_factory=lambda: {"week_ending": None, "email_to": [], "email_from": ""})
    smtp: dict = field(
        default_factory=lambda: {"host": "", "port": 587, "username": "", "password_env": "SMTP_PASSWORD"}
    )


def load_config(path: str | Path | None) -> Config:
    cfg = Config()
    if path and Path(path).exists():
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for key, value in data.items():
            if not hasattr(cfg, key):
                raise ValueError(f"unknown config key: {key}")
            current = getattr(cfg, key)
            if isinstance(current, dict) and isinstance(value, dict):
                current.update(value)
            else:
                setattr(cfg, key, value)
    return cfg
