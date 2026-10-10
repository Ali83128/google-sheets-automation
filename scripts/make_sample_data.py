"""Generate the messy sample order export used by the demo (fixed seed)."""
import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)
ROOT = Path(__file__).resolve().parent.parent
PRODUCTS = {"Desk Lamp": 39.0, "Office Chair": 189.0, "Monitor Arm": 74.5, "USB-C Dock": 129.0, "Keyboard": 59.9}
REGIONS = ["north", "South", "EAST ", " west"]
FIRST = ["anna", "BEN", "carla", "david", "Emma", "farid", "grace", "hassan", "ines", "jamal", "kate", "liam"]
LAST = ["smith", "O'NEIL", "garcia", "khan", "  müller", "brown", "lee", "rossi"]
START = date(2026, 9, 14)  # Monday


def fmt_date(d: date) -> str:
    style = random.random()
    if style < 0.55:
        return d.isoformat()
    if style < 0.8:
        return d.strftime("%d/%m/%Y")
    if style < 0.95:
        return d.strftime("%b %d, %Y")
    return d.strftime("%Y/%m/%d")


def phone() -> str:
    n = "".join(random.choice("0123456789") for _ in range(7))
    area = random.choice(["212", "415", "312", "646"])
    style = random.random()
    if style < 0.4:
        return f"({area}) {n[:3]}-{n[3:]}"
    if style < 0.7:
        return f"+1 {area} {n[:3]} {n[3:]}"
    if style < 0.9:
        return f"{area}.{n[:3]}.{n[3:]}"
    return ""


rows = []
for i in range(1, 121):
    d = START + timedelta(days=random.randint(0, 27))
    first, last = random.choice(FIRST), random.choice(LAST)
    product = random.choice(list(PRODUCTS))
    local_last = last.strip().lower().replace(chr(39), "").replace("ü", "ue")
    email = f"{first.strip().lower()}.{local_last}@example.com"
    if random.random() < 0.25:
        email = "  " + email.upper() + " "
    rows.append({
        "order_id": f"A-{1000 + i}",
        "date": fmt_date(d),
        "customer": f"  {first}   {last}",
        "email": email,
        "phone": phone(),
        "product": product.lower() if random.random() < 0.3 else product,
        "region": random.choice(REGIONS),
        "qty": str(random.choice([1, 1, 1, 2, 2, 3])),
        "unit_price": f"${PRODUCTS[product]:,.2f}" if random.random() < 0.3 else str(PRODUCTS[product]),
        "status": random.choice(["paid", "paid", "paid", "Paid ", "shipped", "refunded"]),
    })

# deliberate problems the clean job must catch
rows[5]["email"] = rows[5]["email"].strip().lower().replace(".com", "")  # invalid email (no TLD)
rows[17]["phone"] = "12345"                        # invalid phone
rows[29]["date"] = "next tuesday"                  # unreadable date
rows[44]["qty"] = "0"                              # non-positive qty
rows[61]["email"] = ""                             # missing email
rows.append(dict(rows[10]))                        # exact duplicate row
dup = dict(rows[20]); dup["status"] = "shipped"; rows.append(dup)  # same order re-exported later
dbl = dict(rows[33]); dbl["order_id"] = "A-1999"; dbl["status"] = "paid"; rows.append(dbl)  # double submission, new ID

with open(ROOT / "data" / "orders_export.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

# an "API" response for the second sync: 3 status updates + 4 new orders
api = []
for idx in (3, 8, 12):
    r = dict(rows[idx]); r["status"] = "refunded" if idx == 8 else "shipped"; api.append(r)
for j in range(4):
    d = START + timedelta(days=26 + j % 2)
    product = list(PRODUCTS)[j]
    api.append({"order_id": f"A-{1200 + j}", "date": d.isoformat(), "customer": "Mia Torres",
                "email": "mia.torres@example.com", "phone": "+1 415 555 0199", "product": product,
                "region": "West", "qty": "1", "unit_price": str(PRODUCTS[product]), "status": "paid"})
(ROOT / "data" / "api_orders.json").write_text(json.dumps({"orders": api}, indent=1), encoding="utf-8")
print(f"wrote {len(rows)} csv rows, {len(api)} api orders")
