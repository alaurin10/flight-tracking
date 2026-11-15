"""The report: one static HTML file, rewritten at the end of every run.

No framework, no build step, no external requests — it must open from a file
on a phone with no signal. Everything is inline: tokens for light and dark,
SVG charts drawn to scale in Python, and ~2 KB of JavaScript for hover
readouts, the destination filter and the grid/table toggle.

Reading order follows the three questions: fixed trips first (should I book?),
then the cheapest fares right now, then the date grids (which weekend?), then
announced deals, then whether the collector itself is healthy.
"""

from __future__ import annotations

import html as _html
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .health import check as health_check
from .report import Advice, GridRow, advise, cheapest, fmt_date, fmt_money, grid, history

# ---------------------------------------------------------------------------
# Tokens. The chart colours are the validated reference palette (blue
# sequential ramp, fixed status colours); the chrome is a cool neutral so the
# blue of the data is the only warm-vs-cool contrast on the page.
# ---------------------------------------------------------------------------

CSS = """
:root {
  color-scheme: light;
  --page: #f3f5f8; --surface: #ffffff; --surface-2: #f7f9fb;
  --ink: #151a21; --ink-2: #4b5563; --muted: #7c8794; --line: #e2e7ed; --hair: #eceff3;
  --accent: #2a78d6; --accent-ink: #1c5cab; --accent-wash: rgba(42,120,214,0.10);
  --good: #0ca30c; --good-ink: #006300; --warn: #fab219; --warn-ink: #7a5200; --crit: #d03b3b; --crit-ink: #a12b2b;
  --h1: #cde2fb; --h2: #9ec5f4; --h3: #6da7ec; --h4: #3987e5; --h5: #256abf; --h6: #184f95;
  --h-ink-lo: #151a21; --h-ink-hi: #ffffff;
  --shadow: 0 1px 2px rgba(21,26,33,0.06), 0 8px 24px -16px rgba(21,26,33,0.25);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0e1116; --surface: #161a20; --surface-2: #1b2028;
    --ink: #f2f4f6; --ink-2: #b8c0ca; --muted: #8b95a1; --line: #262c35; --hair: #20252d;
    --accent: #3987e5; --accent-ink: #86b6ef; --accent-wash: rgba(57,135,229,0.16);
    --good: #0ca30c; --good-ink: #3ec13e; --warn: #fab219; --warn-ink: #fab219; --crit: #e66767; --crit-ink: #e66767;
    --h1: #0d366b; --h2: #184f95; --h3: #256abf; --h4: #3987e5; --h5: #6da7ec; --h6: #9ec5f4;
    --h-ink-lo: #ffffff; --h-ink-hi: #0b0b0b;
    --shadow: none;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0e1116; --surface: #161a20; --surface-2: #1b2028;
  --ink: #f2f4f6; --ink-2: #b8c0ca; --muted: #8b95a1; --line: #262c35; --hair: #20252d;
  --accent: #3987e5; --accent-ink: #86b6ef; --accent-wash: rgba(57,135,229,0.16);
  --good: #0ca30c; --good-ink: #3ec13e; --warn: #fab219; --warn-ink: #fab219; --crit: #e66767; --crit-ink: #e66767;
  --h1: #0d366b; --h2: #184f95; --h3: #256abf; --h4: #3987e5; --h5: #6da7ec; --h6: #9ec5f4;
  --h-ink-lo: #ffffff; --h-ink-hi: #0b0b0b;
  --shadow: none;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent-ink); text-decoration: none; }
a:hover { text-decoration: underline; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
.wrap { max-width: 1080px; margin: 0 auto; padding-block: 0 72px; padding-inline: 16px; }

/* header */
.top {
  position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5;
  background: color-mix(in srgb, var(--page) 88%, transparent); backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--line); margin-inline: -16px; padding: 10px 16px;
}
.top .row { max-width: 1080px; margin: 0 auto; display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.brand { font-weight: 650; letter-spacing: 0.01em; display: flex; align-items: center; gap: 8px; }
.brand svg { width: 18px; height: 18px; }
.asof { color: var(--muted); font-size: 13px; }
.pill {
  display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px;
  font-size: 12px; font-weight: 600; letter-spacing: 0.02em; border: 1px solid var(--line); background: var(--surface); white-space: nowrap;
}
.pill.good { color: var(--good-ink); border-color: color-mix(in srgb, var(--good) 45%, transparent); }
.pill.warn { color: var(--warn-ink); border-color: color-mix(in srgb, var(--warn) 55%, transparent); }
.pill.crit { color: var(--crit-ink); border-color: color-mix(in srgb, var(--crit) 55%, transparent); }
.pill.neutral { color: var(--ink-2); }
.pill .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.spacer { flex: 1; }

/* filters — one row, scopes everything below */
.filters { display: flex; gap: 8px; flex-wrap: wrap; padding-block: 18px 6px; }
.chip {
  border: 1px solid var(--line); background: var(--surface); color: var(--ink-2); border-radius: 999px;
  padding: 5px 12px; font: inherit; font-size: 13px; cursor: pointer;
}
.chip[aria-pressed="true"] { background: var(--ink); color: var(--page); border-color: var(--ink); }

/* sections */
h2 { font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); margin: 34px 0 12px; font-weight: 650; }
h2 .meta { text-transform: none; letter-spacing: 0; font-weight: 400; margin-left: 10px; }
.empty { color: var(--muted); font-style: italic; padding: 14px 2px; }

/* trip cards */
.trips { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; }
.trip {
  background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 18px 18px 14px;
  box-shadow: var(--shadow); display: flex; flex-direction: column; gap: 12px;
}
.trip .head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.trip .name { font-weight: 650; font-size: 17px; }
.trip .dates { color: var(--ink-2); font-size: 13px; margin-top: 2px; }
.trip .hero { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.trip .price { font-size: 40px; font-weight: 650; letter-spacing: -0.02em; line-height: 1.05; }
.trip .delta { font-size: 13px; color: var(--ink-2); }
.trip .delta.down { color: var(--good-ink); }
.trip .delta.up { color: var(--crit-ink); }
.verdict { font-size: 12px; font-weight: 700; letter-spacing: 0.04em; }
.verdict.book { color: var(--good-ink); border-color: color-mix(in srgb, var(--good) 45%, transparent); }
.verdict.lean { color: var(--good-ink); }
.verdict.hold { color: var(--ink-2); }
.verdict.wait { color: var(--warn-ink); border-color: color-mix(in srgb, var(--warn) 55%, transparent); }
.reasons { margin: 0; padding-left: 18px; color: var(--ink-2); font-size: 13px; }
.reasons li { margin: 2px 0; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.stat { background: var(--surface-2); border-radius: 8px; padding: 8px 10px; }
.stat .k { font-size: 11px; color: var(--muted); letter-spacing: 0.04em; text-transform: uppercase; }
.stat .v { font-weight: 600; font-size: 14px; margin-top: 1px; }
.trip .actions { display: flex; gap: 8px; margin-top: 2px; }
.btn {
  display: inline-flex; align-items: center; gap: 6px; padding: 7px 12px; border-radius: 8px; font-size: 13px; font-weight: 600;
  background: var(--accent); color: #fff; border: 1px solid transparent;
}
.btn:hover { text-decoration: none; filter: brightness(1.06); }
.btn.ghost { background: transparent; color: var(--accent-ink); border-color: var(--line); }

/* charts */
.chart { position: relative; }
.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
.chart .tip {
  position: absolute; pointer-events: none; background: var(--ink); color: var(--page); font-size: 12px;
  padding: 4px 8px; border-radius: 6px; white-space: nowrap; transform: translate(-50%, -100%); top: 0; left: 0; display: none;
}
.chart .tip b { font-weight: 700; }
.chart .x { stroke: var(--muted); stroke-width: 1; display: none; }
.chart .m { fill: var(--accent); stroke: var(--surface); stroke-width: 2; display: none; }
.grid-l { stroke: var(--hair); stroke-width: 1; }
.axis-t { fill: var(--muted); font-size: 11px; }
.ln { fill: none; stroke: var(--accent); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.ar { fill: var(--accent); opacity: 0.10; }
.end { fill: var(--accent); stroke: var(--surface); stroke-width: 2; }
.tgt { stroke: var(--good); stroke-width: 1; stroke-dasharray: 3 4; }
.tgt-t { fill: var(--good-ink); font-size: 11px; }
details.tbl summary { color: var(--muted); font-size: 12px; cursor: pointer; margin-top: 4px; }

/* cheapest now */
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.tile { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; display: block; color: inherit; }
.tile:hover { text-decoration: none; border-color: var(--accent); }
.tile .k { font-size: 12px; color: var(--muted); }
.tile .v { font-size: 24px; font-weight: 650; letter-spacing: -0.01em; margin: 2px 0; }
.tile .d { font-size: 12px; color: var(--ink-2); }
.tile .d.low { color: var(--good-ink); font-weight: 600; }

/* heat grids */
.gridcard { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px 12px; margin-bottom: 14px; }
.gridcard .gh { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.gridcard .gh .t { font-weight: 650; }
.gridcard .gh .s { color: var(--muted); font-size: 13px; }
.gridcard .gh .toggle { margin-left: auto; }
.chip.sm { padding: 3px 9px; font-size: 12px; }
.cells { display: grid; grid-template-columns: repeat(auto-fill, minmax(112px, 1fr)); gap: 6px; }
.cell {
  display: block; border-radius: 8px; padding: 8px 9px 7px; color: var(--h-ink-lo); line-height: 1.25; position: relative;
  border: 2px solid transparent;
}
.cell:hover { text-decoration: none; filter: brightness(1.04); }
.cell .d { font-size: 11px; opacity: 0.85; }
.cell .p { font-size: 17px; font-weight: 650; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.cell .s { font-size: 10.5px; opacity: 0.85; display: flex; gap: 6px; }
.cell.h1 { background: var(--h1); } .cell.h2 { background: var(--h2); } .cell.h3 { background: var(--h3); }
.cell.h4 { background: var(--h4); color: var(--h-ink-hi); } .cell.h5 { background: var(--h5); color: var(--h-ink-hi); }
.cell.h6 { background: var(--h6); color: var(--h-ink-hi); }
.cell.best { border-color: var(--ink); }
.cell .tag { position: absolute; top: -8px; right: 8px; font-size: 10px; font-weight: 700; letter-spacing: 0.04em;
  background: var(--ink); color: var(--page); border-radius: 999px; padding: 1px 7px; }
.legend { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 11px; margin-top: 10px; }
.legend .ramp { display: inline-flex; height: 8px; border-radius: 4px; overflow: hidden; width: 96px; }
.legend .ramp i { flex: 1; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); padding: 8px 8px; border-bottom: 1px solid var(--line); font-weight: 600; }
td { padding: 8px 8px; border-bottom: 1px solid var(--hair); font-variant-numeric: tabular-nums; font-size: 14px; }
tr:last-child td { border-bottom: 0; }
td.num, th.num { text-align: right; }
.tw { overflow-x: auto; }
.at-low { color: var(--good-ink); font-weight: 600; }
.up { color: var(--ink-2); }

/* deals */
.deal { display: flex; gap: 12px; align-items: baseline; padding: 10px 2px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.deal:last-child { border-bottom: 0; }
.deal .price { font-weight: 650; min-width: 64px; }
.deal .title { flex: 1 1 320px; }
.deal .when { color: var(--muted); font-size: 12px; }

/* collector */
.collector { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; }
.collector .krow { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 12px; }
.problem { display: flex; gap: 8px; align-items: baseline; font-size: 13px; padding: 4px 0; }
.problem .sev { font-weight: 700; font-size: 11px; letter-spacing: 0.06em; }
.problem.crit .sev { color: var(--crit-ink); } .problem.warn .sev { color: var(--warn-ink); }
.bars { display: flex; align-items: stretch; gap: 4px; height: 44px; margin-top: 8px; }
.bar { flex: 1; height: 100%; display: flex; flex-direction: column-reverse; gap: 2px; min-width: 6px; max-width: 24px; }
.bar i { display: block; border-radius: 3px 3px 0 0; }
.bar .ok { background: var(--good); } .bar .bad { background: var(--crit); }
.bar-l { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted); }
.lg { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--muted); margin-right: 10px; }
.lg i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }

footer { margin-top: 44px; color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); padding-top: 14px; }
[hidden] { display: none !important; }
@media (max-width: 640px) {
  .stats { grid-template-columns: repeat(2, 1fr); }
  .trip .price { font-size: 34px; }
  .cells { grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); }
}
@media (prefers-reduced-motion: no-preference) {
  .cell, .tile, .chip { transition: border-color 120ms ease, filter 120ms ease; }
}
"""

JS = r"""
(function () {
  // Destination filter: one row, scopes every section below it.
  var chips = document.querySelectorAll('.chip[data-filter]');
  function apply(sel) {
    chips.forEach(function (c) { c.setAttribute('aria-pressed', c.dataset.filter === sel ? 'true' : 'false'); });
    document.querySelectorAll('[data-label]').forEach(function (el) {
      el.hidden = !(sel === 'all' || el.dataset.label === sel);
    });
    try { localStorage.setItem('ft.filter', sel); } catch (e) {}
  }
  chips.forEach(function (c) { c.addEventListener('click', function () { apply(c.dataset.filter); }); });
  var saved = 'all';
  try { saved = localStorage.getItem('ft.filter') || 'all'; } catch (e) {}
  if (!document.querySelector('.chip[data-filter="' + saved + '"]')) saved = 'all';
  if (chips.length) apply(saved);

  // Grid / table toggle per date grid.
  document.querySelectorAll('[data-toggle]').forEach(function (b) {
    b.addEventListener('click', function () {
      var card = b.closest('.gridcard');
      var showTable = card.querySelector('.tw').hidden;
      card.querySelector('.tw').hidden = !showTable;
      card.querySelector('.cells').hidden = showTable;
      card.querySelector('.legend').hidden = showTable;
      b.textContent = showTable ? 'Grid' : 'Table';
      b.setAttribute('aria-pressed', showTable ? 'true' : 'false');
    });
  });

  // Line-chart hover: the crosshair finds the nearest date; one readout.
  document.querySelectorAll('.chart[data-points]').forEach(function (ch) {
    var pts = JSON.parse(ch.dataset.points);   // [[x, y, label, value], ...] in SVG units
    var svg = ch.querySelector('svg'), x = ch.querySelector('.x'), m = ch.querySelector('.m'), tip = ch.querySelector('.tip');
    if (!pts.length || !svg) return;
    var vb = svg.viewBox.baseVal;
    function show(i) {
      var p = pts[i], r = svg.getBoundingClientRect(), sx = r.width / vb.width, sy = r.height / vb.height;
      x.setAttribute('x1', p[0]); x.setAttribute('x2', p[0]); x.style.display = 'block';
      m.setAttribute('cx', p[0]); m.setAttribute('cy', p[1]); m.style.display = 'block';
      tip.textContent = ''; var b = document.createElement('b'); b.textContent = p[3]; tip.appendChild(b);
      tip.appendChild(document.createTextNode(' ' + p[2]));
      tip.style.left = (p[0] * sx) + 'px'; tip.style.top = (p[1] * sy - 8) + 'px'; tip.style.display = 'block';
    }
    function hide() { x.style.display = 'none'; m.style.display = 'none'; tip.style.display = 'none'; }
    function nearest(clientX) {
      var r = svg.getBoundingClientRect(), vx = (clientX - r.left) / r.width * vb.width, best = 0, d = 1e9;
      pts.forEach(function (p, i) { var dd = Math.abs(p[0] - vx); if (dd < d) { d = dd; best = i; } });
      return best;
    }
    var cur = pts.length - 1;
    ch.addEventListener('pointermove', function (e) { cur = nearest(e.clientX); show(cur); });
    ch.addEventListener('pointerleave', hide);
    ch.setAttribute('tabindex', '0');
    ch.addEventListener('focus', function () { show(cur); });
    ch.addEventListener('blur', hide);
    ch.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowLeft') { cur = Math.max(0, cur - 1); show(cur); e.preventDefault(); }
      if (e.key === 'ArrowRight') { cur = Math.min(pts.length - 1, cur + 1); show(cur); e.preventDefault(); }
    });
  });
})();
"""

PLANE = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
         'stroke-linejoin="round" aria-hidden="true"><path d="M2 16l20-8-6 9-3-4-4 2-2-2 4-2-9 5z"/></svg>')


def e(text) -> str:
    return _html.escape("" if text is None else str(text), quote=True)


# ---------------------------------------------------------------------------
# Charts (SVG drawn to scale; hover layer wired by JS through data-points)
# ---------------------------------------------------------------------------

def _nice_ticks(lo: float, hi: float, n: int = 3) -> list[int]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** int(len(str(int(raw))) - 1) if raw >= 1 else 1
    step = max(mag, round(raw / mag) * mag)
    start = int(lo // step) * step
    ticks = []
    t = start
    while t <= hi + step and len(ticks) < n + 2:
        if t >= lo - step:
            ticks.append(int(t))
        t += step
    return ticks


def line_chart(series: list[tuple[str, int]], target: int | None = None, width: int = 480, height: int = 150) -> str:
    """Price history: 2px line, 10% area wash, end marker with a surface ring,
    three hairline gridlines, clean tick values, and a hover readout."""
    if len(series) < 2:
        return ""
    pad_l, pad_r, pad_t, pad_b = 44, 12, 14, 24
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    ts = [datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ") for t, _ in series]
    ys = [p / 100 for _, p in series]
    lo, hi = min(ys), max(ys)
    if target:
        lo, hi = min(lo, target / 100), max(hi, target / 100)
    span = (hi - lo) or 1
    lo, hi = lo - span * 0.12, hi + span * 0.12
    t0, t1 = ts[0], ts[-1]
    tspan = (t1 - t0).total_seconds() or 1

    def X(t):
        return pad_l + (t - t0).total_seconds() / tspan * w

    def Y(v):
        return pad_t + (hi - v) / (hi - lo) * h

    pts = [(round(X(t), 1), round(Y(v), 1)) for t, v in zip(ts, ys)]
    path = "M" + " L".join(f"{x},{y}" for x, y in pts)
    area = f"M{pts[0][0]},{pad_t + h} L" + " L".join(f"{x},{y}" for x, y in pts) + f" L{pts[-1][0]},{pad_t + h} Z"
    ticks = [t for t in _nice_ticks(lo, hi, 3) if lo <= t <= hi]
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="price history">']
    for t in ticks:
        y = Y(t)
        out.append(f'<line class="grid-l" x1="{pad_l}" x2="{width - pad_r}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="axis-t" x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end">${t:,}</text>')
    if target:
        y = Y(target / 100)
        out.append(f'<line class="tgt" x1="{pad_l}" x2="{width - pad_r}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="tgt-t" x="{width - pad_r}" y="{y - 4:.1f}" text-anchor="end">target ${target // 100:,}</text>')
    out.append(f'<path class="ar" d="{area}"/><path class="ln" d="{path}"/>')
    for i in (0, len(ts) // 2, len(ts) - 1):
        anchor = "start" if i == 0 else "end" if i == len(ts) - 1 else "middle"
        out.append(f'<text class="axis-t" x="{pts[i][0]}" y="{height - 8}" text-anchor="{anchor}">{ts[i]:%b %d}</text>')
    out.append(f'<circle class="end" cx="{pts[-1][0]}" cy="{pts[-1][1]}" r="4.5"/>')
    out.append(f'<line class="x" y1="{pad_t}" y2="{pad_t + h}"/><circle class="m" r="5"/></svg><div class="tip"></div>')
    data = [[x, y, f"{t:%a %b %d}", f"${v:,.0f}"] for (x, y), t, v in zip(pts, ts, ys)]
    return f'<div class="chart" data-points="{e(json.dumps(data, separators=(",", ":")))}">' + "".join(out) + "</div>"


def history_table(series: list[tuple[str, int]]) -> str:
    rows = "".join(f"<tr><td>{e(t[:10])}</td><td class='num'>{fmt_money(p)}</td></tr>" for t, p in series[-30:])
    return f'<details class="tbl"><summary>History as a table (last {min(30, len(series))} observations)</summary><div class="tw"><table><thead><tr><th>Observed</th><th class="num">Price</th></tr></thead><tbody>{rows}</tbody></table></div></details>'


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _health_pill(rep) -> str:
    if rep is None:
        return ""
    if rep.ok and not rep.problems:
        return '<span class="pill good"><span class="dot"></span>Collector healthy</span>'
    if rep.ok:
        return f'<span class="pill warn"><span class="dot"></span>{len(rep.problems)} warning{"s" if len(rep.problems) != 1 else ""}</span>'
    return '<span class="pill crit"><span class="dot"></span>Collector needs attention</span>'


def _verdict_pill(v: str) -> str:
    cls = {"BOOK": "book", "LEAN BOOK": "lean", "HOLD": "hold", "WAIT": "wait"}.get(v, "hold")
    icon = {"BOOK": "✓", "LEAN BOOK": "↗", "HOLD": "•", "WAIT": "…"}.get(v, "•")
    return f'<span class="pill verdict {cls}">{icon} {e(v)}</span>'


def trip_cards(conn: sqlite3.Connection, cfg, window_days: int) -> str:
    if cfg is None or not cfg.trips:
        return ""
    cards = []
    for trip in cfg.trips:
        items = advise(conn, pattern=trip.name, window_days=window_days, route_window_days=cfg.alerts.percentile_window_days,
                       targets={trip.name: trip.target_price} if trip.target_price else None)
        route = cfg.route(trip.dest)
        label = route.label if route else trip.dest
        if not items:
            cards.append(
                f'<div class="trip" data-label="{e(label)}"><div class="head"><div><div class="name">{e(trip.name.replace("_", " ").title())} · {e(trip.dest)}</div>'
                f'<div class="dates">{e(fmt_date(trip.depart))} → {e(fmt_date(trip.ret))}</div></div></div>'
                f'<p class="empty">No price yet — the next run will fetch it.</p></div>'
            )
            continue
        a: Advice = items[0]
        r = a.row
        series = history(conn, r.query_id, limit=1000)
        delta_html = ""
        if a.trend_7d_pct is not None:
            cls = "down" if a.trend_7d_pct <= -0.5 else "up" if a.trend_7d_pct >= 0.5 else ""
            arrow = "↓" if cls == "down" else "↑" if cls == "up" else "→"
            delta_html = f'<span class="delta {cls}">{arrow} {abs(a.trend_7d_pct):.0f}% vs a week ago</span>'
        reasons = "".join(f"<li>{e(x)}</li>" for x in a.reasons)
        target = trip.target_price or r.target_price
        cards.append(
            f'<div class="trip" data-label="{e(label)}">'
            f'<div class="head"><div><div class="name">{e(label)} · {e(trip.dest)}</div>'
            f'<div class="dates">{e(fmt_date(r.depart_date))} → {e(fmt_date(r.return_date))} · {a.days_to_departure} days out</div></div>'
            f'{_verdict_pill(a.verdict)}</div>'
            f'<div class="hero"><span class="price">{fmt_money(r.current_cents)}</span>{delta_html}</div>'
            f'<ul class="reasons">{reasons}</ul>'
            f'<div class="stats">'
            f'<div class="stat"><div class="k">Target</div><div class="v">{fmt_money(target)}</div></div>'
            f'<div class="stat"><div class="k">All-time low</div><div class="v">{fmt_money(a.all_time_low)}</div></div>'
            f'<div class="stat"><div class="k">{window_days}d low</div><div class="v">{fmt_money(r.low_cents)}</div></div>'
            f'<div class="stat"><div class="k">Observations</div><div class="v">{a.n_obs}</div></div>'
            f'</div>'
            f'{line_chart(series, target=target)}'
            f'{history_table(series) if len(series) > 1 else ""}'
            f'<div class="actions">'
            + (f'<a class="btn" href="{e(r.deep_link)}" target="_blank" rel="noopener">Open in Google Flights</a>' if r.deep_link else "")
            + f'</div></div>'
        )
    return '<h2>Your trips<span class="meta">fixed dates · should you book?</span></h2><div class="trips">' + "".join(cards) + "</div>"


def cheapest_tiles(conn: sqlite3.Connection, window_days: int, exclude_patterns: set[str]) -> str:
    """The best current fare in each date grid (label × pattern), cheapest first."""
    best: dict[tuple[str, str], GridRow] = {}
    for r in grid(conn, window_days=window_days):
        if r.pattern in exclude_patterns or r.current_cents is None:
            continue
        key = (r.label, r.pattern)
        if key not in best or r.current_cents < best[key].current_cents:
            best[key] = r
    rows = sorted(best.values(), key=lambda r: r.current_cents)[:6]
    if not rows:
        return ""
    tiles = []
    for r in rows:
        pct = r.vs_low_pct
        d = f'<div class="d low">at the {window_days}-day low</div>' if r.is_at_low else (
            f'<div class="d">{pct:+.0f}% vs {window_days}d low {fmt_money(r.low_cents)}</div>' if pct is not None else '<div class="d">first observation</div>')
        inner = (f'<div class="k">{e(r.label)} · {e(r.pattern.replace("_", " "))}</div><div class="v">{fmt_money(r.current_cents)}</div>'
                 f'<div class="d">{e(r.dest)} · {e(fmt_date(r.depart_date))} → {e(fmt_date(r.return_date))}</div>{d}')
        if r.deep_link:
            tiles.append(f'<a class="tile" data-label="{e(r.label)}" href="{e(r.deep_link)}" target="_blank" rel="noopener">{inner}</a>')
        else:
            tiles.append(f'<div class="tile" data-label="{e(r.label)}">{inner}</div>')
    return '<h2>Best in each grid<span class="meta">the cheapest date of every pattern, right now</span></h2><div class="tiles">' + "".join(tiles) + "</div>"


def _heat_class(price: int, lo: int, hi: int) -> str:
    if hi <= lo:
        return "h1"
    frac = (price - lo) / (hi - lo)
    return f"h{min(6, 1 + int(frac * 6))}"


def grid_cards(conn: sqlite3.Connection, window_days: int, exclude_patterns: set[str]) -> str:
    rows = [r for r in grid(conn, window_days=window_days) if r.pattern not in exclude_patterns]
    if not rows:
        return ""
    groups: dict[tuple[str, str], list[GridRow]] = {}
    for r in rows:
        groups.setdefault((r.label, r.pattern), []).append(r)
    out = ['<h2>Date grids<span class="meta">every tracked departure · shade is price, see each scale · outlined is cheapest</span></h2>']
    for (label, pattern), prows in sorted(groups.items()):
        priced = [r for r in prows if r.current_cents is not None]
        prices = [r.current_cents for r in priced]
        if not prices:
            continue
        lo, hi = min(prices), max(prices)
        best_ids = {r.query_id for r in priced if r.current_cents == lo}
        dests = sorted({r.dest for r in prows})
        nights = None
        if prows[0].return_date:
            nights = (date.fromisoformat(prows[0].return_date) - date.fromisoformat(prows[0].depart_date)).days
        sub = f"{', '.join(dests)} · {len(priced)} dates · " + (f"{nights} nights" if nights else "one-way") + f" · {fmt_money(lo)} – {fmt_money(hi)}"
        cells = []
        for r in sorted(priced, key=lambda r: r.depart_date):
            cls = _heat_class(r.current_cents, lo, hi)
            flags = []
            if r.is_at_low:
                flags.append("low")
            elif r.vs_low_pct is not None and r.vs_low_pct >= 0.5:
                flags.append(f"+{r.vs_low_pct:.0f}%")
            if r.target_price and r.current_cents <= r.target_price:
                flags.append("★ target")
            if r.sweep_cents is not None:
                flags.append(f"sweep {fmt_money(r.sweep_cents)}")
            tag = '<span class="tag">cheapest</span>' if r.query_id in best_ids else ""
            title = f"{fmt_date(r.depart_date)} → {fmt_date(r.return_date)} · {fmt_money(r.current_cents)} · {r.airline or '?'} · {'nonstop' if r.stops == 0 else str(r.stops) + ' stop(s)' if r.stops is not None else ''}"
            dest_prefix = f"{e(r.dest)} · " if len(dests) > 1 else ""
            body = (f'{tag}<div class="d">{dest_prefix}{e(fmt_date(r.depart_date))}</div><div class="p">{fmt_money(r.current_cents)}</div>'
                    f'<div class="s">{"".join(f"<span>{e(f)}</span>" for f in flags)}</div>')
            best = " best" if r.query_id in best_ids else ""
            if r.deep_link:
                cells.append(f'<a class="cell {cls}{best}" href="{e(r.deep_link)}" target="_blank" rel="noopener" title="{e(title)}">{body}</a>')
            else:
                cells.append(f'<div class="cell {cls}{best}" title="{e(title)}">{body}</div>')
        trs = []
        for r in sorted(priced, key=lambda r: r.current_cents):
            vs = "—" if r.vs_low_pct is None else ('<span class="at-low">at low</span>' if r.is_at_low else f'<span class="up">+{r.vs_low_pct:.0f}%</span>')
            dep = e(fmt_date(r.depart_date))
            if r.deep_link:
                dep = f'<a href="{e(r.deep_link)}" target="_blank" rel="noopener">{dep}</a>'
            trs.append(f'<tr>{"<td>" + e(r.dest) + "</td>" if len(dests) > 1 else ""}<td>{dep}</td><td>{e(fmt_date(r.return_date))}</td>'
                       f'<td class="num">{fmt_money(r.current_cents)}</td><td class="num">{fmt_money(r.low_cents)}</td><td class="num">{vs}</td>'
                       f'<td>{e(r.airline or "—")}</td><td class="num">{"—" if r.stops is None else r.stops}</td></tr>')
        table = ('<div class="tw" hidden><table><thead><tr>' + ("<th>Dest</th>" if len(dests) > 1 else "") +
                 f'<th>Depart</th><th>Return</th><th class="num">Price</th><th class="num">{window_days}d low</th><th class="num">vs low</th><th>Airline</th><th class="num">Stops</th>'
                 '</tr></thead><tbody>' + "".join(trs) + "</tbody></table></div>")
        legend = (f'<div class="legend"><span>{fmt_money(lo)}</span><span class="ramp">' + "".join(f'<i style="background:var(--h{i})"></i>' for i in range(1, 7)) +
                  f'</span><span>{fmt_money(hi)}</span><span>· outlined = cheapest · ★ = under target · tap a date to open the search</span></div>')
        out.append(
            f'<div class="gridcard" data-label="{e(label)}"><div class="gh"><span class="t">{e(label)}</span><span class="s">{e(pattern.replace("_", " "))} · {e(sub)}</span>'
            f'<button class="chip sm toggle" type="button" data-toggle aria-pressed="false">Table</button></div>'
            f'<div class="cells">{"".join(cells)}</div>{table}{legend}</div>'
        )
    return "".join(out) if len(out) > 1 else ""


def deals_section(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT title, link, price_cents, watch, seen_at FROM deal_posts WHERE watch IS NOT NULL ORDER BY id DESC LIMIT 12"
    ).fetchall()
    if not rows:
        return ""
    items = []
    for d in rows:
        title = e(d["title"])
        if d["link"]:
            title = f'<a href="{e(d["link"])}" target="_blank" rel="noopener">{title}</a>'
        items.append(f'<div class="deal"><span class="price">{fmt_money(d["price_cents"]) if d["price_cents"] else "—"}</span>'
                     f'<span class="title">{title}</span><span class="pill neutral">{e(d["watch"])}</span><span class="when">{e((d["seen_at"] or "")[:10])}</span></div>')
    return '<h2>Announced deals<span class="meta">from your feeds, matched to your watches</span></h2><div class="collector">' + "".join(items) + "</div>"


def collector_section(conn: sqlite3.Connection, rep, cfg) -> str:
    last = conn.execute("SELECT * FROM run_log ORDER BY id DESC LIMIT 1").fetchone()
    obs = conn.execute("SELECT COUNT(*) FROM observations WHERE is_best = 1").fetchone()[0]
    since = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    per_day: dict[str, dict[str, int]] = {}
    for r in conn.execute("SELECT substr(at,1,10) AS d, outcome, COUNT(*) AS n FROM fetch_attempts WHERE at >= ? GROUP BY d, outcome", (since,)):
        per_day.setdefault(r["d"], {"ok": 0, "bad": 0})
        per_day[r["d"]]["ok" if r["outcome"] in ("ok", "empty") else "bad"] += r["n"]
    days = sorted(per_day)
    bars = ""
    if days:
        mx = max(v["ok"] + v["bad"] for v in per_day.values()) or 1
        bars = '<div class="bars">' + "".join(
            f'<div class="bar" title="{e(d)}: {per_day[d]["ok"]} ok, {per_day[d]["bad"]} failed">'
            f'<i class="ok" style="height:{per_day[d]["ok"] / mx * 100:.0f}%"></i><i class="bad" style="height:{per_day[d]["bad"] / mx * 100:.0f}%"></i></div>'
            for d in days) + f'</div><div class="bar-l"><span>{e(days[0][5:])}</span><span>{e(days[-1][5:])}</span></div>'
        bars += '<div style="margin-top:6px"><span class="lg"><i style="background:var(--good)"></i>ok / empty</span><span class="lg"><i style="background:var(--crit)"></i>failed</span></div>'
    tiles = [
        ("Last run", f'{e(last["started_at"][:16].replace("T", " "))}Z' if last else "never"),
        ("Last run result", (f'{last["succeeded"]}/{last["attempted"]} ok' + (" · aborted" if last["blocked"] else "")) if last else "—"),
        ("Observations", f"{obs:,}"),
        ("Cooldown", "none"),
    ]
    from .fetch import cooldown_active

    until = cooldown_active(conn)
    if until:
        tiles[3] = ("Cooldown", f"until {until[:16].replace('T', ' ')}Z")
    krow = "".join(f'<div class="stat"><div class="k">{e(k)}</div><div class="v">{v}</div></div>' for k, v in tiles)
    problems = "".join(
        f'<div class="problem {p.severity}"><span class="sev">{p.severity.upper()}</span><span>{e(p.message)}</span></div>' for p in (rep.problems if rep else [])
    ) or '<div class="problem"><span class="sev" style="color:var(--good-ink)">OK</span><span>All checks pass.</span></div>'
    return ('<h2>Collector<span class="meta">is the tracker itself working?</span></h2>'
            f'<div class="collector"><div class="krow">{krow}</div>{problems}<div style="margin-top:10px;font-size:12px;color:var(--muted)">Requests per day, last 14 days</div>{bars}</div>')


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def render(conn: sqlite3.Connection, window_days: int = 30, cfg=None, fragment: bool = False) -> str:
    """Build the whole page. `fragment=True` omits the document skeleton
    (for hosts that wrap the page themselves)."""
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rep = None
    if cfg is not None:
        try:
            rep = health_check(conn, cfg)
        except Exception:  # the page must never fail because a check did
            rep = None

    trip_patterns = {t.name for t in cfg.trips} if cfg else set()
    labels = [r["label"] for r in conn.execute("SELECT DISTINCT label FROM routes WHERE active = 1 ORDER BY label")]
    home = cfg.home if cfg else (conn.execute("SELECT origin FROM routes LIMIT 1").fetchone() or ["—"])[0]

    sections = [
        trip_cards(conn, cfg, window_days),
        cheapest_tiles(conn, window_days, trip_patterns),
        grid_cards(conn, window_days, trip_patterns),
        deals_section(conn),
        collector_section(conn, rep, cfg),
    ]
    body = "".join(s for s in sections if s)
    n_obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    if n_obs == 0:
        body = ('<p class="empty">No observations yet. Run <code>flighttrack run</code> (or wait for the scheduled run), '
                'then reload. <code>flighttrack demo</code> renders this page with synthetic data if you want to see it now.</p>') + body

    chips = '<button class="chip" type="button" data-filter="all" aria-pressed="true">All destinations</button>' + "".join(
        f'<button class="chip" type="button" data-filter="{e(l)}" aria-pressed="false">{e(l)}</button>' for l in labels
    )
    demo_note = ""
    if conn.execute("SELECT 1 FROM observations WHERE source = 'demo' LIMIT 1").fetchone():
        demo_note = ' · <strong>synthetic demo data</strong>'

    head = f"<title>flighttrack</title><style>{CSS}</style>"
    content = (
        f'<div class="wrap"><header class="top"><div class="row"><span class="brand">{PLANE}flighttrack</span>'
        f'<span class="asof">{e(home)} · as of {e(generated)}{demo_note}</span><span class="spacer"></span>{_health_pill(rep)}</div></header>'
        f'<div class="filters">{chips}</div>'
        f'{body}'
        f'<footer>Prices are the cheapest itinerary Google Flights showed for each search, with your bag and fare-class settings. '
        f'Verdicts compare today with this tracker\'s own history; they are arithmetic, not forecasts. Tap any date to open the exact search.</footer>'
        f'</div><script>{JS}</script>'
    )
    if fragment:
        return head + content
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            f'{head}</head><body>{content}</body></html>')


def write(conn: sqlite3.Connection, path: str | Path, window_days: int = 30, cfg=None, fragment: bool = False) -> Path:
    """Render and write the page, creating parent directories as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(conn, window_days=window_days, cfg=cfg, fragment=fragment), encoding="utf-8")
    return path
