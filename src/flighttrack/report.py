"""Reporting — pure SQL over `observations`, no network calls.

This is the piece that answers the original complaint: not "what is the minimum
fare over a wide window" but "what does every extended weekend in January cost,
sorted, and is that a good price by this route's own history."
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SPARK = "▁▂▃▄▅▆▇█"
DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class GridRow:
    query_id: int
    dest: str
    label: str
    pattern: str
    depart_date: str
    return_date: str | None
    current_cents: int | None
    current_at: str | None
    low_cents: int | None
    airline: str | None
    stops: int | None
    duration_min: int | None
    target_price: int | None
    deep_link: str | None

    @property
    def vs_low_pct(self) -> float | None:
        if not self.current_cents or not self.low_cents:
            return None
        return (self.current_cents - self.low_cents) / self.low_cents * 100.0

    @property
    def is_at_low(self) -> bool:
        return (
            self.current_cents is not None
            and self.low_cents is not None
            and self.current_cents <= self.low_cents
        )


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_money(cents: int | None) -> str:
    return "—" if cents is None else f"${cents / 100:,.0f}"


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    d = datetime.strptime(iso, "%Y-%m-%d").date()
    return f"{DOW_NAMES[d.weekday()]} {d.strftime('%b %d')}"


def fmt_duration(minutes: int | None) -> str:
    if not minutes:
        return "—"
    return f"{minutes // 60}h{minutes % 60:02d}m"


def grid(
    conn: sqlite3.Connection,
    dest: str | None = None,
    label: str | None = None,
    pattern: str | None = None,
    month: str | None = None,
    window_days: int = 30,
    include_unpriced: bool = False,
) -> list[GridRow]:
    """The date grid: current price per date pair plus its trailing low.

    `is_best = 1` rows are the canonical price series for a query — one point
    per fetch, the cheapest offer of that fetch.
    """
    where = ["q.active = 1", "r.active = 1"]
    params: list[object] = [_cutoff(window_days)]

    if dest:
        where.append("r.destination = ?")
        params.append(dest.upper())
    if label:
        where.append("r.label = ?")
        params.append(label)
    if pattern:
        where.append("q.pattern = ?")
        params.append(pattern)
    if month:
        where.append("substr(q.depart_date, 1, 7) = ?")
        params.append(month)

    sql = f"""
        WITH latest AS (
            SELECT query_id, price_cents, observed_at, airline, stops, duration_min,
                   ROW_NUMBER() OVER (PARTITION BY query_id ORDER BY observed_at DESC, id DESC) AS rn
            FROM observations
            WHERE is_best = 1
        ),
        lows AS (
            SELECT query_id, MIN(price_cents) AS low_cents
            FROM observations
            WHERE is_best = 1 AND observed_at >= ?
            GROUP BY query_id
        )
        SELECT q.id, r.destination, r.label, q.pattern, q.depart_date, q.return_date,
               l.price_cents AS current_cents, l.observed_at AS current_at,
               lo.low_cents, l.airline, l.stops, l.duration_min,
               r.target_price, q.deep_link
        FROM queries q
        JOIN routes r ON r.id = q.route_id
        LEFT JOIN latest l ON l.query_id = q.id AND l.rn = 1
        LEFT JOIN lows lo ON lo.query_id = q.id
        WHERE {' AND '.join(where)}
        ORDER BY q.depart_date ASC, r.destination ASC
    """

    rows = [
        GridRow(
            query_id=r["id"],
            dest=r["destination"],
            label=r["label"],
            pattern=r["pattern"],
            depart_date=r["depart_date"],
            return_date=r["return_date"],
            current_cents=r["current_cents"],
            current_at=r["current_at"],
            low_cents=r["low_cents"],
            airline=r["airline"],
            stops=r["stops"],
            duration_min=r["duration_min"],
            target_price=r["target_price"],
            deep_link=r["deep_link"],
        )
        for r in conn.execute(sql, params)
    ]
    if not include_unpriced:
        rows = [r for r in rows if r.current_cents is not None]
    return rows


def cheapest(conn: sqlite3.Connection, n: int = 10, window_days: int = 30) -> list[GridRow]:
    """The N best current prices across every tracked route.

    The closest thing this system has to "where should I go."
    """
    rows = grid(conn, window_days=window_days)
    rows.sort(key=lambda r: r.current_cents or 1 << 30)
    return rows[:n]


def history(conn: sqlite3.Connection, query_id: int, limit: int = 200) -> list[tuple[str, int]]:
    """The price series for one date pair, oldest first."""
    rows = conn.execute(
        """
        SELECT observed_at, price_cents FROM observations
        WHERE query_id = ? AND is_best = 1
        ORDER BY observed_at ASC, id ASC
        LIMIT ?
        """,
        (query_id, limit),
    ).fetchall()
    return [(r["observed_at"], r["price_cents"]) for r in rows]


def sparkline(values: list[int]) -> str:
    """Render a price series as block characters."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return SPARK[len(SPARK) // 2] * len(values)
    span = hi - lo
    return "".join(SPARK[min(len(SPARK) - 1, int((v - lo) / span * (len(SPARK) - 1)))] for v in values)


# ---------------------------------------------------------------------------
# Terminal rendering
# ---------------------------------------------------------------------------

def render_grid(rows: list[GridRow], title: str, window_days: int = 30) -> str:
    """The table from plan §9."""
    today = datetime.now(timezone.utc).date().isoformat()
    out: list[str] = ["", f"{title}        (as of {today})", ""]

    if not rows:
        out += ["  No observations yet for this slice.", ""]
        return "\n".join(out)

    # Several airports can share a label (HND and NRT are both "Tokyo"), so the
    # destination column only appears when it is actually needed to tell rows apart.
    multi_dest = len({r.dest for r in rows}) > 1
    dest_h = f"{'DEST':<6}" if multi_dest else ""

    header = (
        f"  {dest_h}{'DEPART':<12}{'RETURN':<12}{'CURRENT':>9}{f'{window_days}d LOW':>10}"
        f"{'vs LOW':>9}  {'AIRLINE':<18}{'STOPS':>5}"
    )
    out += [header, "  " + "─" * (len(header) - 2)]

    for r in rows:
        pct = r.vs_low_pct
        vs = "—" if pct is None else ("—" if abs(pct) < 0.5 else f"{pct:+.0f}%")
        marker = f"   ← {window_days}d low" if r.is_at_low else ""
        target = ""
        if r.target_price and r.current_cents and r.current_cents <= r.target_price:
            target = "  ★ under target"
        dest_c = f"{r.dest:<6}" if multi_dest else ""
        out.append(
            f"  {dest_c}{fmt_date(r.depart_date):<12}{fmt_date(r.return_date):<12}"
            f"{fmt_money(r.current_cents):>9}{fmt_money(r.low_cents):>10}{vs:>9}  "
            f"{(r.airline or '—'):<18}{('—' if r.stops is None else r.stops):>5}"
            f"{marker}{target}"
        )

    out.append("")
    return "\n".join(out)


def render_cheapest(rows: list[GridRow], window_days: int = 30) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    out = ["", f"Cheapest current fares across all tracked routes        (as of {today})", ""]
    if not rows:
        out += ["  No observations yet. Run `flighttrack fetch` first.", ""]
        return "\n".join(out)

    header = (
        f"  {'DEST':<6}{'LABEL':<18}{'DEPART':<12}{'RETURN':<12}"
        f"{'CURRENT':>9}{f'{window_days}d LOW':>10}  {'AIRLINE':<18}"
    )
    out += [header, "  " + "─" * (len(header) - 2)]
    for r in rows:
        out.append(
            f"  {r.dest:<6}{r.label[:17]:<18}{fmt_date(r.depart_date):<12}"
            f"{fmt_date(r.return_date):<12}{fmt_money(r.current_cents):>9}"
            f"{fmt_money(r.low_cents):>10}  {(r.airline or '—'):<18}"
        )
    out.append("")
    return "\n".join(out)


def render_sparklines(conn: sqlite3.Connection, rows: list[GridRow], title: str) -> str:
    out = ["", title, ""]
    if not rows:
        out += ["  No observations yet.", ""]
        return "\n".join(out)

    multi_dest = len({r.dest for r in rows}) > 1
    for r in rows:
        series = history(conn, r.query_id)
        values = [p for _, p in series]
        if not values:
            continue
        dest_c = f"{r.dest:<6}" if multi_dest else ""
        out.append(
            f"  {dest_c}{fmt_date(r.depart_date):<12}{fmt_date(r.return_date):<12}"
            f"{sparkline(values):<24} {fmt_money(min(values))} – {fmt_money(max(values))}"
            f"  (n={len(values)}, now {fmt_money(values[-1])})"
        )
    out.append("")
    return "\n".join(out)
