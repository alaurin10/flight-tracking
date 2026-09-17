"""Regenerate a single static HTML file at the end of every run.

No framework, no web app, no API — just write a file and let whatever already
runs on the box serve it. The reason this exists (plan §12): the moment you
actually want this data is idly, from a phone, wondering about January. A CLI
on a home server is unreachable in exactly that moment.
"""

from __future__ import annotations

import html as _html
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .report import GridRow, fmt_date, fmt_money, grid, history, sparkline
from .health import check as health_check

CSS = """
:root {
  --bg: #fbfaf9; --panel: #fff; --ink: #1c1a17; --muted: #6b6560;
  --line: #e7e3df; --good: #0f7a3d; --warn: #b45309; --accent: #1d4ed8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17151a; --panel: #201d24; --ink: #f2efec; --muted: #a49d97;
    --line: #322d38; --good: #4ade80; --warn: #fbbf24; --accent: #93b4ff;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px 16px 64px; background: var(--bg); color: var(--ink);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 1000px; margin: 0 auto; }
h1 { font-size: 21px; margin: 0 0 4px; letter-spacing: -0.01em; }
.sub { color: var(--muted); font-size: 13px; margin-bottom: 28px; }
h2 {
  font-size: 16px; margin: 32px 0 10px; padding-bottom: 6px;
  border-bottom: 1px solid var(--line);
}
h2 .meta { float: right; font-weight: 400; font-size: 12px; color: var(--muted); }
table { width: 100%; border-collapse: collapse; background: var(--panel); border-radius: 8px; overflow: hidden; }
th {
  text-align: left; font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase;
  color: var(--muted); padding: 9px 10px; border-bottom: 1px solid var(--line); font-weight: 600;
}
td { padding: 9px 10px; border-bottom: 1px solid var(--line); font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: 0; }
td.num, th.num { text-align: right; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.price { font-weight: 650; }
.at-low { color: var(--good); font-weight: 650; }
.up { color: var(--warn); }
.spark { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--muted); letter-spacing: -1px; }
.tag {
  display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 99px;
  background: color-mix(in srgb, var(--good) 15%, transparent); color: var(--good);
  margin-left: 6px; vertical-align: middle;
}
.empty { color: var(--muted); font-style: italic; padding: 14px 10px; }
.sweep { color: var(--muted); }
.health { margin: 0 0 20px; padding: 10px 12px; border-radius: 8px; font-size: 13px; }
.health.bad { background: color-mix(in srgb, var(--warn) 14%, transparent); color: var(--warn); }
.health.ok { background: color-mix(in srgb, var(--good) 12%, transparent); color: var(--good); }
.deal { padding: 8px 10px; border-bottom: 1px solid var(--line); }
.deal .when { color: var(--muted); font-size: 12px; margin-left: 8px; }
footer { margin-top: 44px; color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); padding-top: 14px; }
@media (max-width: 620px) {
  body { padding: 16px 12px 48px; }
  .hide-sm { display: none; }
  h2 .meta { float: none; display: block; margin-top: 2px; }
}
"""


def _row_html(r: GridRow, conn: sqlite3.Connection, show_dest: bool = False, show_sweep: bool = False) -> str:
    pct = r.vs_low_pct
    if pct is None:
        vs = '<span class="num">—</span>'
    elif abs(pct) < 0.5:
        vs = '<span class="at-low">at low</span>'
    else:
        vs = f'<span class="up">+{pct:.0f}%</span>'

    price_cls = "price at-low" if r.is_at_low else "price"
    tag = ""
    if r.target_price and r.current_cents and r.current_cents <= r.target_price:
        tag = '<span class="tag">under target</span>'

    depart = _html.escape(fmt_date(r.depart_date))
    if r.deep_link:
        depart = f'<a href="{_html.escape(r.deep_link, quote=True)}" target="_blank" rel="noopener">{depart}</a>'

    values = [p for _, p in history(conn, r.query_id)]
    spark = _html.escape(sparkline(values[-24:])) if len(values) > 1 else ""

    dest_cell = f"<td>{_html.escape(r.dest)}</td>" if show_dest else ""
    sweep_cell = f'<td class="num sweep">{fmt_money(r.sweep_cents)}</td>' if show_sweep else ""

    return (
        "<tr>"
        f"{dest_cell}"
        f"<td>{depart}</td>"
        f"<td>{_html.escape(fmt_date(r.return_date))}</td>"
        f'<td class="num"><span class="{price_cls}">{fmt_money(r.current_cents)}</span>{tag}</td>'
        f'<td class="num">{fmt_money(r.low_cents)}</td>'
        f'<td class="num">{vs}</td>'
        f"{sweep_cell}"
        f'<td class="hide-sm">{_html.escape(r.airline or "—")}</td>'
        f'<td class="num hide-sm">{"—" if r.stops is None else r.stops}</td>'
        f'<td class="spark hide-sm">{spark}</td>'
        "</tr>"
    )


def render(conn: sqlite3.Connection, window_days: int = 30, cfg=None) -> str:
    """Build the whole page as a string. `cfg` enables the health banner."""
    rows = grid(conn, window_days=window_days)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Group by destination label so HND and NRT appear together under "Tokyo",
    # then by pattern within each label.
    groups: dict[str, dict[str, list[GridRow]]] = {}
    for r in rows:
        groups.setdefault(r.label, {}).setdefault(r.pattern, []).append(r)

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>flighttrack</title>",
        f"<style>{CSS}</style></head><body><div class='wrap'>",
        "<h1>Flight price grid</h1>",
        f'<div class="sub">Generated {_html.escape(generated)} · '
        f"{len(rows)} priced date pairs · lows over trailing {window_days} days · "
        "tap a departure date to open the exact Google Flights search</div>",
    ]

    if cfg is not None:
        try:
            rep = health_check(conn, cfg)
            cls = "ok" if rep.ok else "bad"
            text = "Collector healthy" if rep.ok else "Collector needs attention: " + "; ".join(
                p.message for p in rep.problems if p.severity == "crit")
            parts.append(f'<div class="health {cls}">{_html.escape(text)}</div>')
        except Exception:  # the page must never fail because a check did
            pass

    if not rows:
        parts.append(
            '<p class="empty">No observations yet. Run <code>flighttrack fetch</code>, '
            "then regenerate this page.</p>"
        )

    for label in sorted(groups):
        by_pattern = groups[label]
        cheapest_here = min(
            (r.current_cents for pat in by_pattern.values() for r in pat if r.current_cents),
            default=None,
        )
        parts.append(
            f"<h2>{_html.escape(label)}"
            f'<span class="meta">cheapest now {fmt_money(cheapest_here)}</span></h2>'
        )

        for pattern in sorted(by_pattern):
            prows = sorted(by_pattern[pattern], key=lambda r: r.depart_date)
            dests = sorted({r.dest for r in prows})
            parts.append(
                f'<h2 style="font-size:13px;border:0;margin:18px 0 6px;color:var(--muted)">'
                f'{_html.escape(pattern)} · {_html.escape(", ".join(dests))}</h2>'
            )
            show_dest = len(dests) > 1
            show_sweep = any(r.sweep_cents is not None for r in prows)
            parts.append(
                "<table><thead><tr>"
                + ("<th>Dest</th>" if show_dest else "")
                + "<th>Depart</th><th>Return</th>"
                '<th class="num">Current</th>'
                f'<th class="num">{window_days}d low</th>'
                '<th class="num">vs low</th>'
                + ('<th class="num">Sweep</th>' if show_sweep else "")
                + '<th class="hide-sm">Airline</th><th class="num hide-sm">Stops</th>'
                '<th class="hide-sm">History</th></tr></thead><tbody>'
            )
            parts.extend(_row_html(r, conn, show_dest, show_sweep) for r in prows)
            parts.append("</tbody></table>")

    deals = conn.execute(
        "SELECT title, link, price_cents, watch, seen_at FROM deal_posts "
        "WHERE watch IS NOT NULL ORDER BY id DESC LIMIT 15"
    ).fetchall()
    if deals:
        parts.append('<h2>Announced deals<span class="meta">matched from your feeds</span></h2>')
        for d in deals:
            title = _html.escape(d["title"])
            if d["link"]:
                title = f'<a href="{_html.escape(d["link"], quote=True)}" target="_blank" rel="noopener">{title}</a>'
            price = f' · <span class="price">{fmt_money(d["price_cents"])}</span>' if d["price_cents"] else ""
            parts.append(
                f'<div class="deal"><span class="tag">{_html.escape(d["watch"])}</span> {title}{price}'
                f'<span class="when">{_html.escape((d["seen_at"] or "")[:10])}</span></div>'
            )

    last_run = conn.execute(
        "SELECT started_at, attempted, succeeded, failed, blocked, notes "
        "FROM run_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if last_run:
        status = (
            f"Last run {_html.escape(last_run['started_at'])} — "
            f"{last_run['succeeded']}/{last_run['attempted']} succeeded, "
            f"{last_run['failed']} failed"
        )
        if last_run["blocked"]:
            status += " · <strong>ABORTED (possible rate limiting)</strong>"
        if last_run["notes"]:
            status += f" · {_html.escape(last_run['notes'])}"
    else:
        status = "No runs recorded yet."

    parts.append(f"<footer>{status}</footer></div></body></html>")
    return "\n".join(parts)


def write(conn: sqlite3.Connection, path: str | Path, window_days: int = 30, cfg=None) -> Path:
    """Render and write the page, creating parent directories as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(conn, window_days=window_days, cfg=cfg), encoding="utf-8")
    return path
