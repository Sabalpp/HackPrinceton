"""Standalone descriptive report; source-separated data and explicit limits."""

import html
import json
from datetime import date


def initial_view(report):
    metrics = sorted({row["metric"] for row in report["availability"]})
    selected = "steps" if "steps" in metrics else (metrics[0] if metrics else "")
    choices = [r for r in report["availability"] if r["metric"] == selected]
    first = choices[0] if choices else None
    rows = [
        r
        for r in report["daily"]
        if first
        and all(r[k] == first[k] for k in ("subject_id", "source", "source_stream"))
        and selected in r["metrics"]
    ]
    options = "".join(
        "<option"
        + (" selected" if m == selected else "")
        + ' value="'
        + html.escape(m)
        + '">'
        + html.escape(m.replace("_", " "))
        + "</option>"
        for m in metrics
    )
    streams = "".join(
        '<option value="'
        + html.escape("|".join(r[k] for k in ("subject_id", "source", "source_stream")))
        + '">'
        + html.escape(r["source"] + " · stream " + r["source_stream"][:8])
        + "</option>"
        for r in choices
    )
    table = ""
    for r in rows:
        s = r["metrics"][selected]
        values = [
            r["local_date"],
            "Unavailable" if s["value"] is None else f"{s['value']:.2f} {s['unit']}",
            str(s["measurement_count"]),
            s["aggregation"].replace("_", " "),
            "; ".join(s["quality_flags"]) or "Recorded value",
        ]
        table += "<tr>" + "".join("<td>" + html.escape(v) + "</td>" for v in values) + "</tr>"
    valid = [r for r in rows if r["metrics"][selected]["value"] is not None]
    if not valid:
        return options, streams, table, "<p>No unambiguous daily values available.</p>"
    values = [r["metrics"][selected]["value"] for r in valid]
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.15, 1)
    days = [date.fromisoformat(r["local_date"]).toordinal() for r in rows]

    def x(d):
        return 65 + (d - min(days)) * 900 / max(1, max(days) - min(days))

    def y(v):
        return 200 - (v - lo + pad) / (hi - lo + 2 * pad) * 160

    svg = '<svg viewBox="0 0 1040 260" role="img" aria-label="Recorded daily values">'
    for f in (0, 0.5, 1):
        v = lo - pad + (hi - lo + 2 * pad) * f
        svg += f'<line x1="65" x2="980" y1="{y(v)}" y2="{y(v)}" stroke="#dbe3d8"/><text x="7" y="{y(v) + 4}" fill="#61756d" font-size="12">{v:.1f}</text>'
    for r, day in zip(rows, days):
        value = r["metrics"][selected]["value"]
        if value is not None:
            svg += f'<circle cx="{x(day)}" cy="{y(value)}" r="5" fill="#266957"/>'
        svg += f'<text x="{x(day)}" y="232" text-anchor="middle" fill="#61756d" font-size="12">{r["local_date"][5:]}</text>'
    return options, streams, table, svg + "</svg>"


def render(report):
    pipeline = report["pipeline"]
    sources = {row["source"] for row in pipeline["metrics"]}
    metric_count = len({row["metric"] for row in pipeline["metrics"]})
    statuses = {}
    for comparison in report["comparisons"]:
        key = comparison["baseline_status"]
        statuses[key] = statuses.get(key, 0) + 1
    data = json.dumps(report, allow_nan=False).replace("<", "\\u003c")
    limits = "".join("<li>" + html.escape(item) + "</li>" for item in report["limitations"])
    options, streams, daily_table, chart = initial_view(report)
    baseline_label = "Comparisons available" if statuses.get("ready") else "More history needed"
    return (
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Care review · Sensor history</title>
<style>
:root{color-scheme:light;--ink:#183f35;--muted:#61756d;--paper:#f4f5ee;--line:#dbe3d8}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:1200px;margin:auto;padding:32px}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:20px}
header b{font-size:23px}.tag{border:1px solid var(--line);border-radius:40px;padding:6px 14px;font-size:12px;letter-spacing:1px}
h1{font:52px/1.15 Georgia,serif;margin:38px 0 14px}.intro{max-width:780px;color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:28px 0}.card,section{background:#fffefa;border:1px solid var(--line);border-radius:20px;padding:22px}
.card small{display:block;color:var(--muted);text-transform:uppercase;letter-spacing:1px}.card strong{font-size:34px;display:block;margin-top:8px}
section{margin:20px 0}h2{font-size:22px;margin:0 0 10px}label{display:inline-block;margin:0 14px 10px 0;color:var(--muted)}select{display:block;padding:8px;margin-top:5px;max-width:100%;border:1px solid var(--line);border-radius:8px;background:white;color:var(--ink)}
svg{width:100%;height:auto;display:block}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:10px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted)}.scroll{overflow:auto}.note{color:var(--muted);font-size:14px}.badge{display:inline-block;background:#f3e6d1;padding:5px 10px;border-radius:8px}.empty{padding:30px;color:var(--muted)}details{margin:24px 0;color:var(--muted)}
@media(max-width:700px){main{padding:18px}.cards{grid-template-columns:1fr 1fr}h1{font-size:38px}.card{padding:14px}.card strong{font-size:28px}header .tag{max-width:150px;text-align:center}}
</style><main><header><b>Care review</b><span class="tag">REAL DATA · RESEARCH DEMO</span></header>
<h1>One history. Every source visible.</h1><p class="intro">Review recorded heart rate, movement, sleep and nightly signals together. Daily summaries retain their source and coverage limits. Missing measurements remain missing.</p>
<div class="cards"><div class="card"><small>Measurements</small><strong>"""
        + f"{pipeline['current_events']:,}"
        + """</strong></div>
<div class="card"><small>Signal types</small><strong>"""
        + str(metric_count)
        + """</strong></div>
<div class="card"><small>Data paths</small><strong>"""
        + str(len(sources))
        + """</strong></div>
<div class="card"><small>Quarantined records</small><strong>"""
        + str(pipeline["quarantine"])
        + """</strong></div></div>
<section><h2>Recorded daily signals</h2><p class="note">Each plot shows one source stream. Step totals describe recorded intervals; they do not establish full-day wear or inactivity.</p>
<label>Signal<select id="metric">"""
        + options
        + """</select></label><label>Source stream<select id="stream">"""
        + streams
        + """</select></label>
<div id="chart">"""
        + chart
        + """</div><div class="scroll"><table><thead><tr><th>Source date</th><th>Value</th><th>Readings</th><th>Aggregation</th><th>Quality / baseline</th></tr></thead><tbody id="daily">"""
        + daily_table
        + """</tbody></table></div></section>
<section><h2>Personal baseline</h2><p><span class="badge">"""
        + baseline_label
        + """</span></p><p>Comparison requires at least """
        + str(report["min_baseline_days"])
        + """ prior eligible dates from the same signal and source. This is a demonstration eligibility rule, not a validated diagnostic threshold. These comparisons do not establish diabetes or deterioration risk.</p></section>
<section><h2>Data provenance</h2><div class="scroll"><table><thead><tr><th>Path</th><th>Signal</th><th>Values</th><th>Latest recorded time</th></tr></thead><tbody>"""
        + "".join(
            "<tr><td>"
            + html.escape(row["source"])
            + "</td><td>"
            + html.escape(row["metric"].replace("_", " "))
            + "</td><td>"
            + f"{row['count']:,}"
            + "</td><td>"
            + html.escape(row["latest"])
            + "</td></tr>"
            for row in pipeline["metrics"]
        )
        + """</tbody></table></div></section>
<details><summary>Methods and limits</summary><ul>"""
        + limits
        + """</ul></details>
<p class="note">Generated """
        + html.escape(report["generated_at"])
        + """ · Times shown above are recorded observation times; date-only metrics use a labeled storage anchor. This report makes no diagnosis.</p></main>
<script>const report="""
        + data
        + """;
const metric=document.getElementById('metric'),stream=document.getElementById('stream');
const pretty=s=>s.replaceAll('_',' ');const opt=(v,t)=>{const o=document.createElement('option');o.value=v;o.textContent=t;return o};
const metrics=[...new Set(report.availability.map(r=>r.metric))].sort();metric.replaceChildren();metrics.forEach(m=>metric.append(opt(m,pretty(m))));if(metrics.includes('steps'))metric.value='steps';
function chooseStream(){stream.replaceChildren();report.availability.filter(r=>r.metric===metric.value).forEach(r=>{const id=[r.subject_id,r.source,r.source_stream].join('|');stream.append(opt(id,r.source+' · stream '+r.source_stream.slice(0,8)))});draw()}
function draw(){const rows=report.daily.filter(r=>[r.subject_id,r.source,r.source_stream].join('|')===stream.value&&r.metrics[metric.value]);const tbody=document.getElementById('daily');tbody.replaceChildren();
rows.forEach(r=>{const s=r.metrics[metric.value],tr=document.createElement('tr');const c=report.comparisons.find(c=>c.local_date===r.local_date&&c.source===r.source&&c.source_stream===r.source_stream&&c.metric===metric.value);[r.local_date,s.value===null?'Unavailable':Number(s.value).toFixed(2)+' '+s.unit,s.measurement_count,pretty(s.aggregation),[...s.quality_flags,c?.baseline_status||''].filter(Boolean).map(pretty).join('; ')].forEach(t=>{const td=document.createElement('td');td.textContent=t;tr.append(td)});tbody.append(tr)});
const valid=rows.filter(r=>r.metrics[metric.value].value!==null);const chart=document.getElementById('chart');chart.replaceChildren();if(!valid.length){chart.textContent='No unambiguous daily value for this selection.';return}
const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 1040 260');svg.setAttribute('role','img');svg.setAttribute('aria-label',pretty(metric.value)+' by source date');
const vals=valid.map(r=>r.metrics[metric.value].value),lo=Math.min(...vals),hi=Math.max(...vals),pad=Math.max((hi-lo)*.15,1);const dates=rows.map(r=>Date.parse(r.local_date+'T00:00:00Z'));const first=Math.min(...dates),last=Math.max(...dates);const x=i=>65+(dates[i]-first)*900/Math.max(86400000,last-first),y=v=>200-(v-lo+pad)/(hi-lo+2*pad)*160;
function el(type,attrs,text){let e=document.createElementNS(ns,type);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));if(text!==undefined)e.textContent=text;svg.append(e);return e}
[0,.5,1].forEach(f=>{const v=lo-pad+(hi-lo+2*pad)*f;el('line',{x1:65,x2:980,y1:y(v),y2:y(v),stroke:'#dbe3d8'});el('text',{x:7,y:y(v)+4,fill:'#61756d','font-size':12},v.toFixed(1))});
rows.forEach((r,i)=>{const v=r.metrics[metric.value].value;if(v!==null){const dot=el('circle',{cx:x(i),cy:y(v),r:5,fill:'#266957'});const title=document.createElementNS(ns,'title');title.textContent=r.local_date+': '+v;dot.append(title)}el('text',{x:x(i),y:232,'text-anchor':'middle',fill:'#61756d','font-size':12},r.local_date.slice(5))});chart.append(svg)}
metric.addEventListener('change',chooseStream);stream.addEventListener('change',draw);chooseStream();</script></html>"""
    )
