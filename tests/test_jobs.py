from datetime import date
from pathlib import Path

import pytest

from sheetsauto.backend import FakeBackend, RangeUpdate, RateLimitError
from sheetsauto.clean import clean_rows, norm_date, norm_email, norm_phone
from sheetsauto.config import Config, load_config
from sheetsauto.jobs import run_clean, run_report, run_sync
from sheetsauto.quota import BatchWriter, merge_contiguous, with_backoff
from sheetsauto.report import FakeMailer, build_report
from sheetsauto.sync import upsert

ROOT = Path(__file__).resolve().parent.parent
FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%b %d %Y", "%Y/%m/%d"]


def writer_for(backend, **kw):
    sleeps = []
    w = BatchWriter(backend, requests_per_minute=0, sleep=sleeps.append, jitter=lambda: 0.0, **kw)
    w.sleeps = sleeps
    return w


# ---------------- upsert ----------------

def rec(i, status="paid", price="10"):
    return {"order_id": f"A-{i}", "status": status, "unit_price": price}


def test_upsert_inserts_then_updates_without_duplicating():
    fb = FakeBackend()
    w = writer_for(fb)
    r1 = upsert(w, "Orders", [rec(1), rec(2), rec(3)], "order_id")
    assert (r1.inserted, r1.updated) == (3, 0)
    r2 = upsert(w, "Orders", [rec(2, "refunded"), rec(3), rec(4)], "order_id")
    assert (r2.inserted, r2.updated, r2.unchanged) == (1, 1, 1)
    ids = [r[0] for r in fb.tabs["Orders"][1:]]
    assert ids == ["A-1", "A-2", "A-3", "A-4"]
    assert fb.tabs["Orders"][2][1] == "refunded"


def test_upsert_same_id_twice_in_one_import_keeps_last():
    fb = FakeBackend()
    r = upsert(writer_for(fb), "Orders", [rec(1, "paid"), rec(1, "shipped")], "order_id")
    assert r.inserted == 1 and r.duplicate_ids == 1
    assert fb.tabs["Orders"][1] == ["A-1", "shipped", "10"]


def test_upsert_adds_new_columns_to_header():
    fb = FakeBackend()
    w = writer_for(fb)
    upsert(w, "Orders", [rec(1)], "order_id")
    upsert(w, "Orders", [{**rec(1), "carrier": "DHL"}], "order_id")
    assert fb.tabs["Orders"][0] == ["order_id", "status", "unit_price", "carrier"]
    assert fb.tabs["Orders"][1] == ["A-1", "paid", "10", "DHL"]


def test_upsert_requires_id_column():
    with pytest.raises(ValueError):
        upsert(writer_for(FakeBackend()), "Orders", [{"x": "1"}], "order_id")


# ---------------- cleaning rules ----------------

@pytest.mark.parametrize("raw,expected", [
    ("(212) 555-0101", "+12125550101"),
    ("212.555.0101", "+12125550101"),
    ("+44 20 7946 0958", "+442079460958"),
    ("0044 20 7946 0958", "+442079460958"),
])
def test_phone_normalised(raw, expected):
    assert norm_phone(raw, "1") == (expected, None)


def test_phone_invalid_and_optional():
    assert norm_phone("12345")[1] == "invalid phone"
    assert norm_phone("  ") == ("", None)


def test_email_rules():
    assert norm_email("  BEN.Smith@Example.COM ") == ("ben.smith@example.com", None)
    assert norm_email("grace@example")[1] == "invalid email"
    assert norm_email("")[1] == "missing email"


@pytest.mark.parametrize("raw", ["2026-10-04", "04/10/2026", "Oct 04, 2026", "2026/10/04"])
def test_dates_to_iso(raw):
    assert norm_date(raw, FORMATS) == ("2026-10-04", None)


def test_unreadable_date_flagged():
    assert norm_date("next tuesday", FORMATS)[1] == "unreadable date"


HEADER = ["order_id", "date", "customer", "email", "phone", "product", "region", "qty", "unit_price", "status"]


def raw_row(oid, email="a.b@example.com", date_="2026-10-01", qty="1", product="desk lamp", status="Paid "):
    return [oid, date_, "  anna   SMITH ", email, "(212) 555-0101", product, " west", qty, "$1,039.00", status]


def test_clean_normalises_and_routes_invalid_rows():
    res = clean_rows([HEADER, raw_row("A-1"), raw_row("A-2", email="bad@x", qty="0")],
                     id_column="order_id", default_cc="1", date_formats=FORMATS)
    assert len(res.clean) == 1
    row = dict(zip(res.header, res.clean[0]))
    assert row["customer"] == "Anna Smith"
    assert row["region"] == "West" and row["product"] == "Desk Lamp"
    assert row["unit_price"] == "1039.00" and row["status"] == "paid"
    assert res.review[0][-1] == "invalid email; qty must be positive"


def test_canonical_spelling_from_config():
    rows = [HEADER, raw_row("A-1", product="usb-c dock")]
    res = clean_rows(rows, id_column="order_id", default_cc="1", date_formats=FORMATS,
                     canonical_values={"product": ["USB-C Dock"], "region": ["West"]})
    row = dict(zip(res.header, res.clean[0]))
    assert row["product"] == "USB-C Dock" and row["region"] == "West"


def test_clean_dedupes_exact_rows_and_flags_double_submission():
    rows = [HEADER, raw_row("A-1"), raw_row("A-1"), raw_row("A-9")]
    res = clean_rows(rows, id_column="order_id", default_cc="1", date_formats=FORMATS)
    assert res.duplicates_removed == 1
    assert [r[0] for r in res.clean] == ["A-1"]
    assert res.review[0][0] == "A-9"
    assert res.review[0][-1] == "possible double submission of A-1"


def test_clean_keeps_latest_version_of_same_order():
    rows = [HEADER, raw_row("A-1", status="paid"), raw_row("A-1", status="refunded")]
    res = clean_rows(rows, id_column="order_id", default_cc="1", date_formats=FORMATS)
    assert len(res.clean) == 1 and res.clean[0][-1] == "refunded"


# ---------------- report math ----------------

def test_weekly_report_totals_and_change():
    h = ["order_id", "date", "product", "region", "qty", "unit_price", "status"]
    rows = [
        ["1", "2026-09-28", "Lamp", "West", "2", "10.00", "paid"],   # prev week: 20
        ["2", "2026-10-05", "Lamp", "West", "3", "10.00", "paid"],   # this week: 30
        ["3", "2026-10-06", "Chair", "East", "1", "100.00", "shipped"],  # this week: 100
        ["4", "2026-10-07", "Chair", "East", "1", "100.00", "refunded"],  # excluded
        ["5", "2026-10-12", "Lamp", "West", "1", "10.00", "paid"],   # next (incomplete) week
    ]
    rep = build_report(h, rows)  # defaults to last complete week = week of Oct 5
    assert rep.week_start == date(2026, 10, 5)
    assert (rep.revenue, rep.prev_revenue, rep.orders, rep.prev_orders) == (130.0, 20.0, 2, 1)
    assert rep.change(rep.revenue, rep.prev_revenue) == "+550.0%"
    assert rep.by_product[0] == ("Chair", 100.0, 0.0, 1)
    assert rep.by_product[1] == ("Lamp", 30.0, 20.0, 1)
    assert rep.change(100.0, 0.0) == "new"
    assert rep.rows()[2] == ["Revenue", "130.00", "20.00", "+550.0%"]


# ---------------- quota batching + backoff ----------------

def test_merge_contiguous_rows_into_one_range():
    ups = [RangeUpdate("T", 5, [["c"]]), RangeUpdate("T", 3, [["a"]]), RangeUpdate("T", 4, [["b"]]),
           RangeUpdate("T", 9, [["z"]]), RangeUpdate("U", 6, [["x"]])]
    merged = merge_contiguous(ups)
    assert [(m.tab, m.start_row, len(m.rows)) for m in merged] == [("T", 3, 3), ("T", 9, 1), ("U", 6, 1)]


def test_flush_splits_into_requests_under_range_limit():
    fb = FakeBackend()
    w = writer_for(fb, max_ranges_per_request=10)
    for i in range(25):
        w.add(RangeUpdate("T", 2 + i * 2, [[str(i)]]))  # non-contiguous: 25 ranges
    assert w.flush() == 3
    assert fb.requests == 3


def test_throttle_spaces_requests_to_stay_under_per_minute_quota():
    fb = FakeBackend()
    now = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    w = BatchWriter(fb, requests_per_minute=60, sleep=sleep, clock=lambda: now[0], jitter=lambda: 0.0)
    for _ in range(3):
        w.call(lambda: fb.get_values("T"))
    assert sleeps == [1.0, 1.0]


def test_backoff_retries_429_with_exponential_delays():
    fb = FakeBackend(fail_next=3)
    w = writer_for(fb, base_delay=1.0)
    w.call(lambda: fb.get_values("T"))
    assert w.sleeps == [1.0, 2.0, 4.0]
    assert fb.requests == 4


def test_backoff_gives_up_after_max_retries():
    calls = []

    def boom():
        calls.append(1)
        raise RateLimitError("429")

    with pytest.raises(RateLimitError):
        with_backoff(boom, max_retries=2, sleep=lambda s: None, jitter=lambda: 0.0)
    assert len(calls) == 3


def test_backoff_delay_is_capped():
    delays = []
    fb = FakeBackend(fail_next=4)
    with_backoff(lambda: fb.get_values("T"), max_retries=5, base_delay=10, max_delay=30,
                 sleep=delays.append, jitter=lambda: 0.0)
    assert delays == [10, 20, 30, 30]


# ---------------- end to end on the sample data ----------------

def test_full_pipeline_on_sample_data(tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    fb = FakeBackend()
    w = writer_for(fb)
    s1 = run_sync(fb, cfg, w, ROOT)
    assert s1.inserted == 121 and s1.duplicate_ids == 2

    api = Config(source={"type": "api", "path": "data/api_orders.json"})
    s2 = run_sync(fb, api, w, ROOT)
    assert s2.inserted == 4 and s2.updated + s2.unchanged == 3
    assert s2.updated >= 1
    assert len(fb.tabs["Orders"]) == 1 + 125  # header + unique orders, no duplicates

    c = run_clean(fb, cfg, w)
    problems = {r[0]: r[-1] for r in c.review}
    assert len(c.clean) + len(c.review) == 125
    assert problems["A-1006"] == "invalid email"
    assert problems["A-1018"] == "invalid phone"
    assert problems["A-1030"] == "unreadable date"
    assert problems["A-1045"] == "qty must be positive"
    assert problems["A-1062"] == "missing email"
    assert problems["A-1999"] == "possible double submission of A-1034"

    mailer = FakeMailer()
    rep = run_report(fb, cfg, w, mailer)
    assert fb.tabs["Weekly Report"][0][0] == "Weekly report"
    assert mailer.sent[0][0] == ["owner@example.com"]
    assert rep.orders > 0 and rep.revenue > 0


def test_unknown_config_key_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("bakend: fake\n")
    with pytest.raises(ValueError):
        load_config(p)


# ---------------- gspread backend error mapping (no network) ----------------

class _Resp:
    def __init__(self, code):
        self.status_code = code
        self.text = "{}"

    def json(self):
        return {"error": {"code": self.status_code, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}}


def _bare_gspread_backend():
    import gspread

    from sheetsauto.backend import GspreadBackend

    b = object.__new__(GspreadBackend)
    b._gspread = gspread
    return b, gspread


def test_gspread_429_becomes_rate_limit_error():
    b, gspread = _bare_gspread_backend()

    def quota():
        raise gspread.exceptions.APIError(_Resp(429))

    with pytest.raises(RateLimitError):
        b._call(quota)


def test_gspread_other_errors_pass_through():
    b, gspread = _bare_gspread_backend()

    def forbidden():
        raise gspread.exceptions.APIError(_Resp(403))

    with pytest.raises(gspread.exceptions.APIError):
        b._call(forbidden)
