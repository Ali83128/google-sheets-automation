"""Render docs/screenshots from the real output of a demo run (output/*.json, *.log)."""
import html
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
tabs = json.loads((ROOT / "output" / "fake_sheet.json").read_text(encoding="utf-8"))
esc = html.escape

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{width:1440px;height:900px;background:#f1f3f4;font-family:'Segoe UI',Roboto,Arial,sans-serif;color:#202124;overflow:hidden}
.top{height:56px;background:#fff;border-bottom:1px solid #dadce0;display:flex;align-items:center;padding:0 20px;gap:14px}
.logo{width:28px;height:36px;background:#0f9d58;border-radius:3px;position:relative}
.logo:after{content:'';position:absolute;left:6px;right:6px;top:12px;bottom:8px;border:2px solid #fff;border-radius:1px}
.title{font-size:18px}
.chip{margin-left:auto;font-size:12px;font-weight:600;letter-spacing:.6px;color:#b06000;background:#fef7e0;border:1px solid #f9d77e;padding:5px 12px;border-radius:12px}
.wrap{padding:18px 20px}
h2{font-size:14px;font-weight:600;color:#3c4043;margin:6px 0 8px;display:flex;align-items:center;gap:10px}
h2 span{font-weight:400;color:#5f6368}
table{border-collapse:collapse;background:#fff;font-size:12.5px;width:100%;table-layout:fixed}
td,th{border:1px solid #e2e3e3;padding:5px 7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;height:26px}
th{background:#f8f9fa;color:#5f6368;font-weight:400;text-align:center;font-size:11.5px}
td.rn{background:#f8f9fa;color:#5f6368;text-align:center;width:38px}
tr.hdr td{font-weight:600;background:#e8f0fe}
td.chg{background:#e6f4ea;color:#137333}
td.bad{background:#fce8e6;color:#a50e0e}
td.num{text-align:right}
.tabs{position:absolute;left:0;right:0;bottom:0;height:40px;background:#f8f9fa;border-top:1px solid #dadce0;display:flex;align-items:flex-end;padding-left:60px;gap:2px}
.tab{padding:9px 18px;font-size:13px;color:#5f6368}
.tab.on{background:#fff;color:#188038;font-weight:600;border-bottom:3px solid #188038}
.note{font-size:12px;color:#5f6368;margin-top:8px}
"""

COLS = "ABCDEFGHIJKLMNOP"


def sheet(rows, widths, mark=None, start=1, right_cols=()):
    mark = mark or {}
    out = ["<table><colgroup><col style='width:38px'>"]
    out += [f"<col style='width:{w}px'>" for w in widths]
    out.append("</colgroup><tr><th></th>" + "".join(f"<th>{COLS[i]}</th>" for i in range(len(widths))) + "</tr>")
    for n, r in enumerate(rows):
        cls = " class='hdr'" if n == 0 and start == 1 else ""
        cells = []
        for j in range(len(widths)):
            v = r[j] if j < len(r) else ""
            c = mark.get((n, j), "")
            if j in right_cols or (not v.startswith("+") and v.replace(".", "", 1).replace("-", "", 1).replace("%", "").isdigit() and j):
                c += " num"
            cells.append(f"<td class='{' '.join(c.split())}'>{esc(v)}</td>")
        out.append(f"<tr{cls}><td class='rn'>{start + n}</td>{''.join(cells)}</tr>")
    out.append("</table>")
    return "".join(out)


def page(title, body, active):
    tabbar = "".join(f"<div class='tab{' on' if t == active else ''}'>{esc(t)}</div>" for t in tabs)
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
            f"<div class='top'><div class='logo'></div><div class='title'>{esc(title)}</div>"
            f"<div class='chip'>SAMPLE DATA</div></div><div class='wrap'>{body}</div>"
            f"<div class='tabs'>{tabbar}</div></body></html>")


def before_after():
    raw, clean, review = tabs["Orders"], tabs["Clean"], tabs["Review"]
    header = raw[0]
    by_id = {r[0]: r for r in clean[1:]}
    rev_ids = {r[0]: r for r in review[1:]}
    pick = [r for r in raw[1:] if r[0] in by_id][:6]
    widths = [70, 92, 135, 225, 120, 105, 70, 40, 82, 72]
    raw_rows = [header] + pick
    clean_rows = [header] + [by_id[r[0]] for r in pick]
    mark = {}
    for n, (a, b) in enumerate(zip(raw_rows[1:], clean_rows[1:]), start=1):
        for j, (x, y) in enumerate(zip(a, b)):
            if x != y:
                mark[(n, j)] = "chg"
    raw_disp = [header] + [[v.replace(" ", "·") if v != v.strip() or "  " in v else v for v in r] for r in pick]
    rev_rows = [header + ["problem"]] + list(rev_ids.values())
    rev_w = [62, 82, 120, 190, 112, 92, 58, 34, 70, 62, 230]
    rev_mark = {(n, 10): "bad" for n in range(1, len(rev_rows))}
    body = (
        "<h2>Orders <span>raw import, as it arrived (· = stray spaces)</span></h2>"
        + sheet(raw_disp, widths)
        + "<h2 style='margin-top:14px'>Clean <span>same orders after the clean job (changed cells in green)</span></h2>"
        + sheet(clean_rows, widths, mark)
        + f"<h2 style='margin-top:14px'>Review <span>{len(rev_rows) - 1} rows held back for a human, with the reason</span></h2>"
        + sheet(rev_rows, rev_w, rev_mark)
    )
    return page("Orders 2026 - before and after cleaning", body, "Clean")


def report_page():
    rep = tabs["Weekly Report"]
    mark = {}
    for n, r in enumerate(rep):
        if len(r) > 3 and r[3].startswith("+"):
            mark[(n, 3)] = "chg"
        elif len(r) > 3 and r[3].startswith("-") and len(r[3]) > 1:
            mark[(n, 3)] = "bad"
    for n, r in enumerate(rep):
        if r and r[0] in ("Product", "Region", "") and len(r) > 1 and r[1] in ("Revenue", "This week"):
            for j in range(4):
                mark[(n, j)] = mark.get((n, j), "") + " hdrc"
    body = (
        "<h2>Weekly Report <span>built by the report job from the Clean tab; paid and shipped orders only</span></h2>"
        + sheet(rep, [260, 200, 200, 160], mark, right_cols=(3,))
        + "<div class='note'>Week-over-week change: green = up, red = down, \"new\" = no sales the previous week. "
        "The same table is emailed to the owner when the job runs with --email.</div>"
    )
    css_extra = "<style>td.hdrc{font-weight:600;background:#e8f0fe}tr:first-of-type+tr td:nth-child(2){font-weight:600}</style>"
    return page("Orders 2026 - weekly report", body, "Weekly Report").replace("</head>", css_extra + "</head>")


def terminal_page():
    log = (ROOT / "output" / "cli_run.log").read_text(encoding="utf-8").rstrip().splitlines()
    tests = (ROOT / "output" / "pytest_q.log").read_text(encoding="utf-8").rstrip().splitlines()
    verbose = (ROOT / "output" / "pytest.log").read_text(encoding="utf-8").splitlines()
    names = [ln.split("::", 1)[1].split(" PASSED")[0] for ln in verbose if "::" in ln and " PASSED" in ln]
    half = (len(names) + 1) // 2
    grid = []
    for a, b in zip(names[:half], names[half:] + [""]):
        left = f"<span class='ok'>PASSED</span> {esc(a[:58]):<58}"
        right = f"<span class='ok'>PASSED</span> {esc(b[:58])}" if b else ""
        grid.append(f"{left}  {right}")
    lines = []
    for ln in log + ["$ python -m pytest -q"] + tests:
        e = esc(ln)
        if ln.startswith("$"):
            e = f"<span class='p'>{e}</span>"
        elif "passed" in ln:
            e = f"<span class='ok'>{e}</span>"
        else:
            if " INFO " in e:
                ts, rest = e.split(" INFO ", 1)
                pad = rest[: len(rest) - len(rest.lstrip())]
                word, _, tail = rest.lstrip().partition(" ")
                if word in ("sync", "clean", "report", "email", "done"):
                    rest = f"{pad}<span class='k'>{word}</span> {tail}"
                e = f"{ts} <span class='i'>INFO</span> {rest}"
        lines.append(e)
    css = """*{margin:0;padding:0;box-sizing:border-box}
    body{width:1440px;height:900px;background:#0d1117;font-family:'DejaVu Sans Mono','Consolas',monospace;color:#c9d1d9;padding:28px}
    .win{border:1px solid #30363d;border-radius:10px;overflow:hidden;height:844px;background:#010409}
    .bar{background:#161b22;height:38px;display:flex;align-items:center;padding:0 16px;gap:8px;border-bottom:1px solid #30363d}
    .bar i{width:12px;height:12px;border-radius:50%;display:block}.bar span{margin-left:14px;color:#8b949e;font-size:13px}
    .chip{margin-left:auto!important;color:#d29922!important;font-size:11px!important;letter-spacing:1px;border:1px solid #d29922;border-radius:10px;padding:2px 9px}
    pre{padding:16px 22px 4px;font-size:13.2px;line-height:1.55;white-space:pre-wrap}
    pre.grid{font-size:11.6px;line-height:1.5;color:#8b949e;padding-top:6px}
    .p{color:#58a6ff;font-weight:bold}.i{color:#3fb950}.k{color:#d2a8ff;font-weight:bold}.ok{color:#3fb950;font-weight:bold}"""
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body><div class='win'>"
            "<div class='bar'><i style='background:#ff5f56'></i><i style='background:#ffbd2e'></i>"
            "<i style='background:#27c93f'></i><span>google-sheets-automation - real run against the offline fake sheet</span>"
            "<span class='chip'>SAMPLE DATA</span></div>"
            f"<pre>{chr(10).join(lines)}</pre>"
            f"<pre class='grid'><span class='p'>$ python -m pytest -v</span>  ({len(names)} tests)\n{chr(10).join(grid)}</pre>"
            "</div></body></html>")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    shots = {
        "01_before_after_cleaning.png": before_after(),
        "02_weekly_report.png": report_page(),
        "03_cli_run_and_tests.png": terminal_page(),
    }
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        for name, doc in shots.items():
            pg.set_content(doc)
            pg.wait_for_timeout(200)
            pg.screenshot(path=str(OUT / name))
            print("wrote", OUT / name)
        b.close()


if __name__ == "__main__":
    main()
