"""Weekly report job: totals by product and region with week-over-week change."""
from __future__ import annotations

import os
import smtplib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from typing import Protocol


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


@dataclass
class WeeklyReport:
    week_start: date
    prev_start: date
    revenue: float
    prev_revenue: float
    orders: int
    prev_orders: int
    by_product: list[tuple[str, float, float, int]] = field(default_factory=list)
    by_region: list[tuple[str, float, float, int]] = field(default_factory=list)

    @staticmethod
    def change(cur: float, prev: float) -> str:
        if prev == 0:
            return "new" if cur else "0.0%"
        pct = (cur - prev) / prev * 100
        return "0.0%" if abs(pct) < 0.05 else f"{pct:+.1f}%"

    def rows(self) -> list[list[str]]:
        end = self.week_start + timedelta(days=6)
        out = [
            ["Weekly report", f"{self.week_start.isoformat()} to {end.isoformat()}", "", ""],
            ["", "This week", "Previous week", "Change"],
            ["Revenue", f"{self.revenue:.2f}", f"{self.prev_revenue:.2f}", self.change(self.revenue, self.prev_revenue)],
            ["Orders", str(self.orders), str(self.prev_orders), self.change(self.orders, self.prev_orders)],
            ["", "", "", ""],
            ["Product", "Revenue", "Previous week", "Change"],
        ]
        out += [[p, f"{c:.2f}", f"{v:.2f}", self.change(c, v)] for p, c, v, _ in self.by_product]
        out += [["", "", "", ""], ["Region", "Revenue", "Previous week", "Change"]]
        out += [[r, f"{c:.2f}", f"{v:.2f}", self.change(c, v)] for r, c, v, _ in self.by_region]
        return out

    def as_text(self) -> str:
        return "\n".join("  ".join(f"{c:<16}" for c in r).rstrip() for r in self.rows())


def build_report(header: list[str], rows: list[list[str]], week_ending: date | None = None) -> WeeklyReport:
    col = {h: i for i, h in enumerate(header)}
    paid = [r for r in rows if "status" not in col or r[col["status"]] in ("paid", "shipped", "delivered")]
    dated = [(date.fromisoformat(r[col["date"]]), r) for r in paid]
    if not dated:
        raise ValueError("no paid orders to report on")
    if week_ending is None:
        # last complete week in the data: the week before the newest order's week
        week_ending = week_start(max(d for d, _ in dated)) - timedelta(days=1)
    cur_start = week_start(week_ending)
    prev_start = cur_start - timedelta(days=7)

    def amount(r: list[str]) -> float:
        return int(r[col["qty"]]) * float(r[col["unit_price"]])

    totals = {cur_start: defaultdict(float), prev_start: defaultdict(float)}
    counts = {cur_start: 0, prev_start: 0}
    prod = defaultdict(lambda: [0.0, 0.0, 0])
    reg = defaultdict(lambda: [0.0, 0.0, 0])
    for d, r in dated:
        ws = week_start(d)
        if ws not in totals:
            continue
        a = amount(r)
        slot = 0 if ws == cur_start else 1
        totals[ws]["revenue"] += a
        counts[ws] += 1
        prod[r[col["product"]]][slot] += a
        reg[r[col["region"]]][slot] += a
        if slot == 0:
            prod[r[col["product"]]][2] += 1
            reg[r[col["region"]]][2] += 1

    def ranked(d):
        return sorted(((k, round(v[0], 2), round(v[1], 2), v[2]) for k, v in d.items()), key=lambda t: (-t[1], t[0]))

    return WeeklyReport(
        week_start=cur_start,
        prev_start=prev_start,
        revenue=round(totals[cur_start]["revenue"], 2),
        prev_revenue=round(totals[prev_start]["revenue"], 2),
        orders=counts[cur_start],
        prev_orders=counts[prev_start],
        by_product=ranked(prod),
        by_region=ranked(reg),
    )


class Mailer(Protocol):
    def send(self, to: list[str], subject: str, body: str) -> None: ...


class SmtpMailer:
    def __init__(self, host: str, port: int, username: str, password: str, sender: str):
        self.host, self.port, self.username, self.password, self.sender = host, port, username, password, sender

    @classmethod
    def from_config(cls, smtp: dict, sender: str) -> "SmtpMailer":
        return cls(smtp["host"], int(smtp.get("port", 587)), smtp.get("username", ""),
                   os.environ.get(smtp.get("password_env", "SMTP_PASSWORD"), ""), sender)

    def send(self, to: list[str], subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, ", ".join(to), subject
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=30) as s:
            s.starttls()
            if self.username:
                s.login(self.username, self.password)
            s.send_message(msg)


@dataclass
class FakeMailer:
    sent: list[tuple[list[str], str, str]] = field(default_factory=list)

    def send(self, to: list[str], subject: str, body: str) -> None:
        self.sent.append((list(to), subject, body))
