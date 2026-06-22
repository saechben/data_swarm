"""Render a benchmark run as a console summary and a self-contained HTML dashboard."""
from __future__ import annotations

import html
import json
from datetime import datetime

CAP_LABEL = {
    "table_boundaries": "Table boundaries",
    "value_extraction": "Value extraction",
    "semantic_extraction": "Semantic extraction",
    "formula_compute": "Formula (intra-table)",
    "measure_detection": "Measure detection",
}


def _bar(rate: float, width: int = 24) -> str:
    fill = int(round(rate * width))
    return "█" * fill + "·" * (width - fill)


def render_console(payload: dict) -> str:
    s = payload["summary"]
    lines = []
    lines.append("=" * 64)
    lines.append(f" Benchmark — adapter: {s['adapter']}")
    lines.append("=" * 64)
    o = s["overall"]
    lines.append(f" Overall sample pass rate: {o['rate']*100:6.2f}%  "
                 f"({o['passed']}/{o['total']})")
    lines.append("")
    lines.append(" By capability")
    lines.append(" " + "-" * 62)
    for cap in ["table_boundaries", "value_extraction", "semantic_extraction",
                "formula_compute"]:
        c = s["by_capability"][cap]
        lines.append(f"  {CAP_LABEL[cap]:22s} {_bar(c['rate'])} "
                     f"{c['rate']*100:6.2f}%  ({c['passed']}/{c['total']})")
    md = s["by_capability"]["measure_detection"]
    lines.append(f"  {CAP_LABEL['measure_detection']:22s} {_bar(md['f1'])} "
                 f"F1={md['f1']*100:6.2f}%  (P={md['precision']*100:.1f} "
                 f"R={md['recall']*100:.1f}, tp={md['tp']} fp={md['fp']} fn={md['fn']})")
    lines.append("")
    lines.append(" By difficulty")
    lines.append(" " + "-" * 62)
    for tier in ["easy", "medium", "hard"]:
        t = s["by_difficulty"][tier]
        lines.append(f"  {tier:22s} {_bar(t['rate'])} {t['rate']*100:6.2f}%  "
                     f"({t['passed']}/{t['total']})")
    lines.append("")
    lines.append(" By workbook")
    lines.append(" " + "-" * 62)
    for wb, w in sorted(payload["summary"]["by_workbook"].items(),
                        key=lambda kv: (kv[1]["difficulty"], kv[0])):
        lines.append(f"  {wb:36s} [{w['difficulty']:6s}] "
                     f"{w['rate']*100:6.2f}%  ({w['passed']}/{w['total']})")
    lines.append("=" * 64)
    return "\n".join(lines)


def render_html(payload: dict) -> str:
    s = payload["summary"]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    def pct(x):
        return f"{x*100:.1f}%"

    def cap_card(cap, c):
        if cap == "measure_detection":
            val = c["f1"]
            sub = (f"P {pct(c['precision'])} · R {pct(c['recall'])} · "
                   f"tp {c['tp']} fp {c['fp']} fn {c['fn']}")
            head = f"F1 {pct(val)}"
        else:
            val = c["rate"]
            sub = f"{c['passed']}/{c['total']} samples"
            head = pct(val)
        hue = int(val * 120)  # 0 red -> 120 green
        return f"""<div class="card">
          <div class="card-title">{html.escape(CAP_LABEL[cap])}</div>
          <div class="card-val" style="color:hsl({hue},70%,42%)">{head}</div>
          <div class="meter"><span style="width:{val*100:.1f}%;background:hsl({hue},70%,45%)"></span></div>
          <div class="card-sub">{html.escape(sub)}</div>
        </div>"""

    cap_cards = "".join(
        cap_card(cap, s["by_capability"][cap])
        for cap in ["table_boundaries", "value_extraction", "semantic_extraction",
                    "formula_compute", "measure_detection"]
    )

    tier_rows = ""
    for tier in ["easy", "medium", "hard"]:
        t = s["by_difficulty"][tier]
        tier_rows += (f"<tr><td>{tier}</td><td>{pct(t['rate'])}</td>"
                      f"<td>{t['passed']}/{t['total']}</td></tr>")

    wb_rows = ""
    for wb, w in sorted(s["by_workbook"].items(),
                        key=lambda kv: (kv[1]["difficulty"], kv[0])):
        bad = "" if w["rate"] >= 0.999 else ' class="warn"'
        wb_rows += (f"<tr{bad}><td>{html.escape(wb)}</td><td>{w['difficulty']}</td>"
                    f"<td>{pct(w['rate'])}</td><td>{w['passed']}/{w['total']}</td></tr>")

    # failing samples drilldown
    fails = [x for x in payload["samples"] if not x["passed"]]
    fail_rows = ""
    for x in fails:
        fail_rows += (
            f"<tr><td>{html.escape(x['workbook'])}</td>"
            f"<td>{x['sample_type']}</td>"
            f"<td>{html.escape(str(x['expected']))}</td>"
            f"<td>{html.escape(str(x['got']))}</td>"
            f"<td>{html.escape(str(x['detail']))}</td></tr>")
    if not fail_rows:
        fail_rows = '<tr><td colspan="5" class="ok">No failing samples 🎉</td></tr>'

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MCG Swarm Eval — {html.escape(s['adapter'])}</title>
<style>
  :root {{ --bg:#0f1115; --panel:#171a21; --line:#262b36; --txt:#e6e9ef;
           --muted:#8b94a7; --accent:#5b9dff; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--txt);
          font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; padding:32px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); margin-bottom:24px; }}
  .overall {{ font-size:42px; font-weight:700; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
           gap:14px; margin:20px 0 28px; }}
  .card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px;
           padding:16px; }}
  .card-title {{ color:var(--muted); font-size:12px; text-transform:uppercase;
                 letter-spacing:.04em; }}
  .card-val {{ font-size:28px; font-weight:700; margin:6px 0; }}
  .card-sub {{ color:var(--muted); font-size:12px; }}
  .meter {{ height:6px; background:#0b0d12; border-radius:6px; overflow:hidden;
            margin:6px 0; }}
  .meter span {{ display:block; height:100%; }}
  table {{ width:100%; border-collapse:collapse; background:var(--panel);
           border:1px solid var(--line); border-radius:12px; overflow:hidden;
           margin-bottom:28px; }}
  th,td {{ text-align:left; padding:9px 12px; border-bottom:1px solid var(--line);
           font-size:13px; }}
  th {{ color:var(--muted); font-weight:600; background:#12151c; }}
  tr.warn td {{ background:rgba(255,176,32,.07); }}
  td.ok {{ color:#46c46e; text-align:center; }}
  h2 {{ font-size:15px; margin:18px 0 10px; }}
  .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
  @media (max-width:720px) {{ .cols {{ grid-template-columns:1fr; }} }}
</style></head><body>
  <h1>MCG Swarm Benchmark (v2 — Canonical Tables)</h1>
  <div class="sub">adapter <b>{html.escape(s['adapter'])}</b> · {ts}</div>
  <div class="overall" style="color:hsl({int(s['overall']['rate']*120)},70%,55%)">
    {pct(s['overall']['rate'])}</div>
  <div class="sub">overall sample pass rate
    ({s['overall']['passed']}/{s['overall']['total']})</div>
  <div class="grid">{cap_cards}</div>
  <div class="cols">
    <div><h2>By difficulty</h2><table><tr><th>Tier</th><th>Pass rate</th>
      <th>Passed</th></tr>{tier_rows}</table></div>
    <div><h2>By workbook</h2><table><tr><th>Workbook</th><th>Tier</th>
      <th>Rate</th><th>Passed</th></tr>{wb_rows}</table></div>
  </div>
  <h2>Failing samples ({len(fails)})</h2>
  <table><tr><th>Workbook</th><th>Type</th><th>Expected</th><th>Got</th>
    <th>Detail</th></tr>{fail_rows}</table>
</body></html>"""
