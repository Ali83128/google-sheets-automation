"""Clean job: normalise a raw tab, dedupe it, and send invalid rows to a Review tab."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def squash(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def norm_name(value: str) -> str:
    v = squash(value)
    return " ".join(w[:1].upper() + w[1:].lower() if w.isalpha() or "-" in w or "'" in w else w for w in v.split(" "))


def norm_email(value: str) -> tuple[str, str | None]:
    v = squash(value).lower().replace(" ", "")
    if not v:
        return "", "missing email"
    if not EMAIL_RE.match(v):
        return v, "invalid email"
    return v, None


def norm_phone(value: str, default_cc: str = "1") -> tuple[str, str | None]:
    raw = squash(value)
    if not raw:
        return "", None  # phone is optional
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        pass
    elif raw.startswith("00"):
        digits = digits[2:]
    elif len(digits) == 10:
        digits = default_cc + digits
    if not 11 <= len(digits) <= 15:
        return raw, "invalid phone"
    return "+" + digits, None


def norm_date(value: str, formats: list[str]) -> tuple[str, str | None]:
    v = squash(value).replace(",", "")
    if not v:
        return "", "missing date"
    for fmt in formats:
        try:
            return datetime.strptime(v, fmt).date().isoformat(), None
        except ValueError:
            continue
    return v, "unreadable date"


def norm_number(value: str, kind=float) -> tuple[str, str | None]:
    v = squash(value).replace("$", "").replace(",", "")
    if not v:
        return "", "missing number"
    try:
        n = kind(float(v)) if kind is int else float(v)
    except ValueError:
        return value, "not a number"
    if n <= 0:
        return str(n), "must be positive"
    return (str(n) if kind is int else f"{n:.2f}"), None


@dataclass
class CleanResult:
    header: list[str]
    clean: list[list[str]]
    review: list[list[str]]
    duplicates_removed: int


def canonical(value: str, known: list[str] | None) -> str:
    """Map a value onto its official spelling (e.g. "usb-c dock" -> "USB-C Dock")."""
    for k in known or []:
        if k.casefold() == value.casefold():
            return k
    return value.title() if value.islower() or value.isupper() else value


def clean_rows(
    rows: list[list[str]],
    *,
    id_column: str,
    default_cc: str,
    date_formats: list[str],
    canonical_values: dict[str, list[str]] | None = None,
) -> CleanResult:
    if not rows:
        return CleanResult([], [], [], 0)
    header = [squash(h) for h in rows[0]]
    col = {h: i for i, h in enumerate(header)}

    def get(r: list[str], name: str) -> str:
        i = col.get(name)
        return r[i] if i is not None and i < len(r) else ""

    out: dict[str, list[str]] = {}
    review: list[list[str]] = []
    exact_seen: set[tuple] = set()
    signatures: dict[tuple, str] = {}
    duplicates = 0

    for raw in rows[1:]:
        if not any(squash(c) for c in raw):
            continue
        rec = {h: squash(get(raw, h)) for h in header}
        problems: list[str] = []
        if "customer" in rec:
            rec["customer"] = norm_name(rec["customer"])
        if "email" in rec:
            rec["email"], p = norm_email(rec["email"])
            problems += [p] if p else []
        if "phone" in rec:
            rec["phone"], p = norm_phone(rec["phone"], default_cc)
            problems += [p] if p else []
        if "date" in rec:
            rec["date"], p = norm_date(rec["date"], date_formats)
            problems += [p] if p else []
        if "qty" in rec:
            rec["qty"], p = norm_number(rec["qty"], int)
            problems += [f"qty {p}"] if p else []
        if "unit_price" in rec:
            rec["unit_price"], p = norm_number(rec["unit_price"])
            problems += [f"price {p}"] if p else []
        known = canonical_values or {}
        for name in ("product", "region"):
            if name in rec:
                rec[name] = canonical(rec[name], known.get(name))
        if "status" in rec:
            rec["status"] = rec["status"].lower()

        row = [rec[h] for h in header]
        key = tuple(row)
        if key in exact_seen:
            duplicates += 1
            continue
        exact_seen.add(key)

        if problems:
            review.append(row + ["; ".join(problems)])
            continue
        oid = rec.get(id_column, "")
        if oid in out:
            duplicates += 1  # later export of the same order replaces the earlier one
            out[oid] = row
            continue
        sig = tuple(rec.get(k, "") for k in ("email", "product", "date", "qty"))
        if all(sig) and sig in signatures and signatures[sig] != oid:
            review.append(row + [f"possible double submission of {signatures[sig]}"])
            continue
        signatures[sig] = oid
        out[oid] = row

    clean = sorted(out.values(), key=lambda r: (r[col["date"]] if "date" in col else "", r[col.get(id_column, 0)]))
    return CleanResult(header, clean, review, duplicates)
