"""Console entry points.

Cron calls exactly the same commands you would type by hand, so anything that
fails at 3am can be reproduced with one line in a terminal.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import alert as alert_mod
from . import expand as expand_mod
from . import fetch as fetch_mod
from . import html as html_mod
from . import report as report_mod
from .config import Config, ConfigError, load
from .db import connect

DEFAULT_CONFIG = "config.yaml"


def _load(args, strict_secrets: bool = False) -> Config:
    """Load config, exiting with a clear message rather than a traceback.

    Only `alert` needs delivery secrets, so every other command loads leniently.
    """
    try:
        return load(args.config, strict_secrets=strict_secrets)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _db(cfg: Config) -> sqlite3.Connection:
    return connect(cfg.db_path)


# ---------------------------------------------------------------------------
# expand
# ---------------------------------------------------------------------------

def cmd_expand(args) -> int:
    cfg = _load(args)
    conn = _db(cfg)
    result = expand_mod.expand(conn, cfg, build_links=not args.no_links)
    print(f"[expand] {result.summary()}")

    cap = cfg.fetch.max_queries_per_run
    if result.total_active > cap:
        days = (result.total_active + cap - 1) // cap
        print(
            f"[expand] note: {result.total_active} active queries against a "
            f"{cap}/run cap — full coverage cycles about every {days} days. "
            f"Raise fetch.max_queries_per_run after calibrating, or narrow the horizon."
        )
    return 0


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def cmd_fetch(args) -> int:
    cfg = _load(args)
    conn = _db(cfg)

    if args.dry_run:
        due = fetch_mod.select_due(conn, cfg, limit=args.limit)
        print(f"[fetch --dry-run] {len(due)} queries would be fetched:")
        for d in due:
            ret = d.return_date or "one-way"
            age = d.last_fetch_at or "never"
            print(f"  {cfg.home}→{d.route.dest}  {d.depart_date} / {ret}  "
                  f"[{d.pattern}, prio {d.route.priority}, last {age}]")
        return 0

    from .source import LiveSource

    stats = fetch_mod.run(conn, cfg, LiveSource(), limit=args.limit)
    print(f"[fetch] {stats.summary()}")

    # Plan §12: regenerate the static page at the end of every run.
    if not args.no_html:
        path = html_mod.write(conn, cfg.html_path)
        print(f"[fetch] wrote {path}")

    if stats.blocked:
        print(
            "[fetch] run aborted on consecutive failures — treating as possible rate "
            "limiting. Do NOT re-run immediately; let the next scheduled run cool off.",
            file=sys.stderr,
        )
        return 1
    if stats.attempted and stats.succeeded == 0:
        print("[fetch] every attempt failed — check `flighttrack doctor`", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# alert
# ---------------------------------------------------------------------------

def cmd_alert(args) -> int:
    cfg = _load(args, strict_secrets=True)
    conn = _db(cfg)
    stats = alert_mod.run(conn, cfg, dry_run=args.dry_run)
    return 1 if stats.delivery_error else 0


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def cmd_report(args) -> int:
    cfg = _load(args)
    conn = _db(cfg)

    if args.cheapest:
        rows = report_mod.cheapest(conn, n=args.cheapest, window_days=args.window)
        print(report_mod.render_cheapest(rows, window_days=args.window))
        return 0

    rows = report_mod.grid(
        conn,
        dest=args.dest,
        label=args.label,
        pattern=args.pattern,
        month=args.month,
        window_days=args.window,
        include_unpriced=args.include_unpriced,
    )

    scope = args.label or args.dest or "all routes"
    bits = [f"{cfg.home} → {scope}"]
    if args.pattern:
        bits.append(args.pattern.replace("_", " "))
    if args.month:
        bits.append(datetime.strptime(args.month, "%Y-%m").strftime("%B %Y"))
    title = " · ".join(bits)

    if args.sparkline:
        print(report_mod.render_sparklines(conn, rows, f"{title} — price history"))
    else:
        print(report_mod.render_grid(rows, title, window_days=args.window))
    return 0


# ---------------------------------------------------------------------------
# html
# ---------------------------------------------------------------------------

def cmd_html(args) -> int:
    cfg = _load(args)
    conn = _db(cfg)
    path = html_mod.write(conn, args.out or cfg.html_path, window_days=args.window)
    print(f"[html] wrote {path}")
    return 0


# ---------------------------------------------------------------------------
# status — the health monitor. Silent failure is the worst outcome here.
# ---------------------------------------------------------------------------

def cmd_status(args) -> int:
    cfg = _load(args)
    conn = _db(cfg)

    active = conn.execute(
        "SELECT COUNT(*) FROM queries q JOIN routes r ON r.id = q.route_id "
        "WHERE q.active = 1 AND r.active = 1"
    ).fetchone()[0]
    obs = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    span = conn.execute("SELECT MIN(observed_at), MAX(observed_at) FROM observations").fetchone()
    never = conn.execute(
        "SELECT COUNT(*) FROM queries q JOIN routes r ON r.id = q.route_id "
        "WHERE q.active = 1 AND r.active = 1 AND q.last_fetch_at IS NULL"
    ).fetchone()[0]

    print(f"\nactive queries : {active}  ({never} never fetched)")
    print(f"observations   : {obs}")
    if span and span[0]:
        print(f"history span   : {span[0]} → {span[1]}")
    print(f"database       : {cfg.db_path}\n")

    print(f"{'RUN':>4}  {'STARTED':<21}{'ATT':>4}{'OK':>4}{'FAIL':>5}{'BLOCK':>6}  NOTES")
    rows = conn.execute(
        "SELECT * FROM run_log ORDER BY id DESC LIMIT ?", (args.limit,)
    ).fetchall()
    if not rows:
        print("  no runs recorded yet — has cron fired?")
    for r in rows:
        flag = "YES" if r["blocked"] else ""
        print(
            f"{r['id']:>4}  {r['started_at']:<21}{r['attempted']:>4}{r['succeeded']:>4}"
            f"{r['failed']:>5}{flag:>6}  {r['notes'] or ''}"
        )

    # A run that never finished means the process died mid-flight.
    unfinished = [r["id"] for r in rows if r["finished_at"] is None]
    if unfinished:
        print(f"\n  ⚠ runs with no finish time (process died?): {unfinished}")
    recent_blocked = [r["id"] for r in rows if r["blocked"]]
    if recent_blocked:
        print(f"  ⚠ aborted runs (possible rate limiting): {recent_blocked}")
    print()
    return 0


# ---------------------------------------------------------------------------
# doctor — milestone 1, runnable on the host that has network access
# ---------------------------------------------------------------------------

def cmd_doctor(args) -> int:
    """Verify the data source end to end and report the price units.

    This exists as a command because the environment this project was built in
    could not reach Google (egress policy), so the one thing that cannot be
    verified offline is deferred to the machine that will actually run it.
    """
    print("\n=== flighttrack doctor ===\n")
    ok = True

    # 1. config
    try:
        cfg = load(args.config, strict_secrets=False)
        print(f"[ok]   config          : {len(cfg.routes)} routes, {len(cfg.patterns)} patterns")
    except ConfigError as exc:
        print(f"[FAIL] config          : {exc}")
        return 1

    # Delivery is checked but never fatal here — doctor diagnoses the data
    # source, and you may well run it before setting up notifications.
    channel = (cfg.notify or {}).get("channel", "none")
    if not cfg.alerts.enabled:
        print("[warn] notifications   : alerts are disabled in config")
    elif channel == "ntfy" and not (cfg.notify.get("ntfy") or {}).get("topic"):
        print("[warn] notifications   : ntfy selected but no topic — set $NTFY_TOPIC "
              "before `flighttrack alert`")
    else:
        print(f"[ok]   notifications   : {channel}")

    # 2. dependency
    try:
        import fast_flights
        from importlib.metadata import version

        v = version("fast-flights")
        print(f"[ok]   fast-flights    : {v}")
        if v != "3.1.0":
            print("       ⚠ expected 3.1.0 — an unpinned upgrade can silently change")
            print("         the response shape and corrupt the observation history.")
    except Exception as exc:
        print(f"[FAIL] fast-flights    : {exc}")
        return 1

    # 3. offline query construction
    route = cfg.routes[0]
    depart, ret = args.depart, args.ret
    try:
        from .source import build_query, deep_link

        q = build_query(cfg, route, depart, ret)
        link = deep_link(cfg, route, depart, ret)
        print(f"[ok]   query build     : tfs is {len(q.to_str())} chars (offline, no network)")
        print(f"       deep link       : {link[:96]}...")
    except Exception as exc:
        print(f"[FAIL] query build     : {exc}")
        return 1

    if args.offline:
        print("\n--offline given: skipping the live call.\n")
        return 0

    # 4. the live call — the actual milestone-1 gate
    print(f"\n--- live fetch: {cfg.home}→{route.dest} {depart}" + (f" / {ret}" if ret else "") + " ---")
    try:
        from fast_flights import get_flights

        t0 = time.monotonic()
        result = get_flights(q)
        elapsed = time.monotonic() - t0
        offers = list(result)
        print(f"[ok]   live fetch      : {len(offers)} itineraries in {elapsed:.1f}s")
    except Exception as exc:
        print(f"[FAIL] live fetch      : {type(exc).__name__}: {exc}")
        print(
            "\n  This is the milestone-1 gate. If it is a network/proxy error, the host\n"
            "  cannot reach Google. If it is a parse error, the page shape changed and\n"
            "  fast-flights needs attention — check its issue tracker before upgrading.\n"
        )
        return 1

    if not offers:
        print("[warn] live fetch returned zero itineraries — try a nearer date")
        return 1

    # 5. the units question, answered
    from .source import _to_offer, normalize_price, price_is_plausible

    n_legs = 2 if ret else 1
    print("\n--- price units (confirm these) ---")
    for f in offers[:3]:
        raw = getattr(f, "price", None)
        cents = normalize_price(raw)
        segs = list(getattr(f, "flights", []) or [])
        print(
            f"  raw={raw!r} ({type(raw).__name__})  ->  {cents} cents = ${cents/100:,.2f}"
            f"   plausible={price_is_plausible(cents)}"
        )
        print(
            f"      airlines={getattr(f, 'airlines', None)}  segments={len(segs)}"
            f"  legs_requested={n_legs}  derived_stops={max(0, len(segs) - n_legs)}"
            f"  segment_durations={[getattr(s, 'duration', None) for s in segs]}"
        )

    normalized = _to_offer(offers[0], cfg.search.currency, n_legs)
    print(f"\n  cheapest normalized: {normalized}")

    all_cents = [normalize_price(getattr(f, "price", None)) for f in offers]
    if not all(price_is_plausible(c) for c in all_cents):
        ok = False
        print(
            "\n  ⚠ Some normalized prices are outside the plausible band. If the raw\n"
            "    values above look like CENTS already (e.g. 81100 for an $811 fare),\n"
            "    upstream units changed: flip assume_major_units in source.py and\n"
            "    re-derive any affected history from the stored raw JSON."
        )
    else:
        print(
            "\n  If the dollar amounts above match what google.com/travel/flights shows\n"
            "  for this search, price normalization is correct and milestone 1 passes."
        )

    print()
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# calibrate — plan §7. Measure the rate limit; do not assume it.
# ---------------------------------------------------------------------------

def cmd_calibrate(args) -> int:
    """Issue N queries at a fixed interval and log the outcome of each.

    The entire pacing design is contingent on the number this produces, and
    that number is not published anywhere — it is Google's per-IP behaviour.
    """
    cfg = load(args.config)
    if not args.yes:
        print(
            f"About to issue up to {args.count} real requests to Google Flights at "
            f"{args.interval}s intervals.\nThis is the calibration sweep from plan §7. "
            f"Re-run with --yes to proceed."
        )
        return 2

    from .source import LiveSource, NoResults, SourceError

    route = cfg.routes[0]
    source = LiveSource()
    base = datetime.strptime(args.depart, "%Y-%m-%d").date()

    results: list[tuple[int, str, float]] = []
    first_failure_at: int | None = None
    print(f"\n[calibrate] {args.count} requests @ {args.interval}s, {cfg.home}→{route.dest}\n")

    for i in range(args.count):
        if i:
            time.sleep(args.interval)
        # Vary the date so this is a realistic sweep, not a cached repeat.
        depart = (base + timedelta(days=i)).isoformat()
        ret = (base + timedelta(days=i + 4)).isoformat()

        t0 = time.monotonic()
        try:
            offers = source.fetch(cfg, route, depart, ret, 1)
            dt = time.monotonic() - t0
            status = f"ok ({offers[0].price_cents/100:,.0f})"
        except NoResults as exc:
            dt = time.monotonic() - t0
            status = f"empty: {exc}"
        except SourceError as exc:
            dt = time.monotonic() - t0
            status = f"FAIL: {exc}"
            if first_failure_at is None:
                first_failure_at = i + 1
        results.append((i + 1, status, dt))
        print(f"  {i+1:>3}/{args.count}  {dt:5.1f}s  {status}")

        if first_failure_at and args.stop_on_failure:
            print("\n  stopping at first failure (--stop-on-failure)")
            break

    oks = [d for _, s, d in results if s.startswith("ok")]
    fails = sum(1 for _, s, _ in results if s.startswith("FAIL"))
    print(f"\n[calibrate] {len(oks)} ok, {fails} failed, {len(results) - len(oks) - fails} empty")
    if oks:
        print(f"[calibrate] latency: min {min(oks):.1f}s median {statistics.median(oks):.1f}s max {max(oks):.1f}s")
    if first_failure_at:
        print(f"[calibrate] FIRST FAILURE at request #{first_failure_at} — this is the number that matters")
        print("[calibrate] set fetch.max_queries_per_run well below it, and record it in the README")
    else:
        print(f"[calibrate] no failures at {args.interval}s spacing across {len(results)} requests")
        print("[calibrate] you may raise fetch.max_queries_per_run toward this, with margin")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = ["n,status,seconds"] + [f"{n},{s},{d:.2f}" for n, s, d in results]
        out.write_text("\n".join(lines) + "\n")
        print(f"[calibrate] wrote {out}")
    return 0


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flighttrack", description="Self-hosted daily flight price sensor."
    )
    p.add_argument("--config", default=DEFAULT_CONFIG, help=f"config file (default {DEFAULT_CONFIG})")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("expand", help="materialise date patterns into the queries table")
    e.add_argument("--no-links", action="store_true", help="skip deep-link construction")
    e.set_defaults(func=cmd_expand)

    f = sub.add_parser("fetch", help="fetch due queries and append observations")
    f.add_argument("--limit", type=int, default=None, help="override max queries this run")
    f.add_argument("--dry-run", action="store_true", help="show what would be fetched")
    f.add_argument("--no-html", action="store_true", help="skip regenerating the HTML report")
    f.set_defaults(func=cmd_fetch)

    a = sub.add_parser("alert", help="evaluate fresh observations and notify")
    a.add_argument("--dry-run", action="store_true", help="print instead of sending")
    a.set_defaults(func=cmd_alert)

    r = sub.add_parser("report", help="the date-grid table and other slices")
    r.add_argument("--dest", help="filter to one destination airport, e.g. HND")
    r.add_argument("--label", help="filter to one label, e.g. Tokyo (covers HND+NRT)")
    r.add_argument("--pattern", help="filter to one pattern, e.g. extended_weekend_thu")
    r.add_argument("--month", help="filter departures to YYYY-MM")
    r.add_argument("--window", type=int, default=30, help="trailing-low window in days")
    r.add_argument("--cheapest", type=int, metavar="N", help="N best current fares across all routes")
    r.add_argument("--sparkline", action="store_true", help="show price history per date pair")
    r.add_argument("--include-unpriced", action="store_true", help="also show never-fetched pairs")
    r.set_defaults(func=cmd_report)

    h = sub.add_parser("html", help="regenerate the static HTML report")
    h.add_argument("--out", help="output path (default from config)")
    h.add_argument("--window", type=int, default=30)
    h.set_defaults(func=cmd_html)

    s = sub.add_parser("status", help="run history and health")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_status)

    d = sub.add_parser("doctor", help="verify the data source and confirm price units")
    d.add_argument("--depart", default=None, help="departure date YYYY-MM-DD")
    d.add_argument("--ret", default=None, help="return date YYYY-MM-DD")
    d.add_argument("--offline", action="store_true", help="skip the live call")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("calibrate", help="measure the rate limit (plan §7)")
    c.add_argument("--count", type=int, default=20)
    c.add_argument("--interval", type=float, default=6.0)
    c.add_argument("--depart", default=None)
    c.add_argument("--stop-on-failure", action="store_true")
    c.add_argument("--out", default=None, help="write per-request CSV here")
    c.add_argument("--yes", action="store_true", help="required: confirms real requests")
    c.set_defaults(func=cmd_calibrate)

    return p


def _default_dates(args) -> None:
    """Fill in sensible dates for doctor/calibrate: mid-horizon, 4-night trip."""
    if getattr(args, "depart", None) is None:
        args.depart = (date.today() + timedelta(days=60)).isoformat()
    if getattr(args, "ret", None) is None and args.cmd == "doctor":
        args.ret = (
            datetime.strptime(args.depart, "%Y-%m-%d").date() + timedelta(days=4)
        ).isoformat()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd in ("doctor", "calibrate"):
        _default_dates(args)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
