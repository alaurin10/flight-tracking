"""Console entry points.

Cron calls exactly the same commands you would type by hand, so anything that
fails at 3am can be reproduced with one line in a terminal. `flighttrack run`
is the one-line daily job; the individual commands exist so each stage can be
run, and fail, on its own.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import alert as alert_mod
from . import deals as deals_mod
from . import expand as expand_mod
from . import fetch as fetch_mod
from . import health as health_mod
from . import html as html_mod
from . import report as report_mod
from . import sweep as sweep_mod
from .config import Config, ConfigError, load
from .db import connect

DEFAULT_CONFIG = "config.yaml"


def _load(args, strict_secrets: bool = False) -> Config:
    try:
        return load(args.config, strict_secrets=strict_secrets)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _db(cfg: Config) -> sqlite3.Connection:
    return connect(cfg.db_path)


def _source(cfg: Config):
    from .source import build_source

    return build_source(cfg, failure_dir=cfg.failure_dir)


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
        hint = "enable calendar.enabled for daily sweeps, " if not cfg.calendar.enabled else ""
        print(
            f"[expand] note: {result.total_active} active queries against a {cap}/run cap — "
            f"full detail coverage cycles about every {days} days. {hint}raise the cap after "
            f"calibrating, or narrow the horizon."
        )
    return 0


# ---------------------------------------------------------------------------
# fetch / sweep
# ---------------------------------------------------------------------------

def cmd_fetch(args) -> int:
    cfg = _load(args)
    conn = _db(cfg)

    if args.dry_run:
        until = fetch_mod.cooldown_active(conn)
        if until:
            print(f"[fetch --dry-run] would SKIP: in cooldown until {until}")
        due = fetch_mod.select_due(conn, cfg, limit=args.limit)
        print(f"[fetch --dry-run] {len(due)} queries would be fetched:")
        for d in due:
            ret = d.return_date or "one-way"
            age = d.last_fetch_at or "never"
            print(f"  {cfg.home}→{d.route.dest}  {d.depart_date} / {ret}  [{d.pattern}, prio {d.route.priority}, last {age}]")
        return 0

    stats = fetch_mod.run(conn, cfg, _source(cfg), limit=args.limit, respect_cooldown=not args.ignore_cooldown)
    print(f"[fetch] {stats.summary()}")

    if not args.no_html:
        path = html_mod.write(conn, cfg.html_path, cfg=cfg)
        print(f"[fetch] wrote {path}")

    return _fetch_exit(stats)


def _fetch_exit(stats: fetch_mod.FetchStats) -> int:
    if stats.skipped:
        return 0
    if stats.blocked:
        print(
            f"[fetch] run aborted ({stats.abort_reason}). "
            + ("A cooldown is now in effect; the next scheduled run will skip itself until it expires."
               if stats.cooldown_until else "No cooldown set (layout failures are not helped by waiting) — run `flighttrack doctor`."),
            file=sys.stderr,
        )
        return 1
    if stats.attempted and stats.succeeded == 0 and stats.empty == 0:
        print("[fetch] every attempt failed — check `flighttrack doctor`", file=sys.stderr)
        return 1
    return 0


def cmd_sweep(args) -> int:
    cfg = _load(args)
    conn = _db(cfg)
    if not cfg.calendar.enabled and not args.force:
        print("[sweep] calendar.enabled is false — verify with `flighttrack doctor --calendar`, then enable it, or pass --force")
        return 2
    if args.dry_run:
        jobs = sweep_mod.plan(cfg)
        print(f"[sweep --dry-run] {len(jobs)} windows planned ({cfg.calendar.max_calls_per_run}/run):")
        for j in jobs:
            print(f"  {j.describe()}")
        return 0
    stats = sweep_mod.run(conn, cfg, confirm_source=_source(cfg))
    print(f"[sweep] {stats.summary()}")
    if stats.confirm_stats:
        print(f"[sweep] confirm: {stats.confirm_stats.summary()}")
    if not args.no_html:
        html_mod.write(conn, cfg.html_path, cfg=cfg)
    return 1 if stats.blocked or (stats.calls and stats.ok == 0) else 0


# ---------------------------------------------------------------------------
# alert / deals / health
# ---------------------------------------------------------------------------

def cmd_alert(args) -> int:
    cfg = _load(args, strict_secrets=True)
    conn = _db(cfg)
    stats = alert_mod.run(conn, cfg, dry_run=args.dry_run)
    return 1 if stats.delivery_error else 0


def cmd_deals(args) -> int:
    cfg = _load(args, strict_secrets=not (args.dry_run or args.test_match))
    conn = _db(cfg)
    if args.test_match:
        from .deals import FeedItem, first_match

        item = FeedItem(guid="test", title=args.test_match, link=None, published=None, summary="", feed="test")
        w = first_match(item, cfg.deals.watches)
        print(f"{'MATCH ' + w.name if w else 'no match'}: {args.test_match}")
        return 0
    stats = deals_mod.run(conn, cfg, dry_run=args.dry_run)
    return 1 if stats.delivery_error or (stats.feeds and stats.feeds_ok == 0) else 0


def cmd_health(args) -> int:
    cfg = _load(args)
    conn = _db(cfg)
    rep, notified = health_mod.run(conn, cfg, dry_run=args.dry_run or not args.notify)
    if notified and not args.dry_run and args.notify:
        print("[health] notification sent")
    return 0 if rep.ok else 1


# ---------------------------------------------------------------------------
# run — the daily job, one line in cron
# ---------------------------------------------------------------------------

def cmd_run(args) -> int:
    """expand → sweep (if enabled) → fetch → deals → alert → health → html, stages isolated."""
    from .jobs import run_daily

    cfg = _load(args, strict_secrets=False)
    conn = _db(cfg)
    result = run_daily(conn, cfg, _source, limit=args.limit, dry_run=args.dry_run)
    print(f"\n[run] {result.summary()}")
    return result.rc


def cmd_serve(args) -> int:
    """Scheduler + web server in one process — the always-on-box way to run this."""
    from .jobs import run_daily
    from .serve import serve

    cfg = _load(args, strict_secrets=False)
    connect(cfg.db_path).close()   # apply schema up front so the first GET has tables

    def job():
        conn = _db(cfg)
        try:
            result = run_daily(conn, cfg, _source, limit=args.limit, dry_run=args.dry_run)
            print(f"[run] {result.summary()}")
        finally:
            conn.close()

    serve(cfg, job, host=args.host, port=args.port, at=args.at, jitter_minutes=args.jitter,
          run_on_start=args.run_on_start)
    return 0


def cmd_compact(args) -> int:
    """Prune runner-up offers and old request logs; never the price series."""
    from .compact import compact

    cfg = _load(args)
    conn = _db(cfg)
    st = compact(conn, cfg, dry_run=not args.yes)
    print(f"[compact] {st.summary()}" + ("" if args.yes else "  (pass --yes to apply)"))
    return 0


def cmd_demo(args) -> int:
    """Seed synthetic history and render the report, to see the UI before real data exists."""
    from . import demo as demo_mod

    cfg = _load(args, strict_secrets=False)
    db_path = Path(args.db)
    if db_path.exists():
        if not args.force:
            print(f"[demo] {db_path} exists — pass --force to overwrite")
            return 2
        db_path.unlink()
    conn = connect(db_path)
    stats = demo_mod.seed(conn, cfg, days=args.days)
    out = html_mod.write(conn, args.out, cfg=cfg)
    print(f"[demo] {stats['queries']} queries, {stats['observations']} synthetic observations over {stats['days']} days")
    print(f"[demo] database {db_path}\n[demo] report   {out}")
    print("[demo] open the report in a browser; every number in it is synthetic (source='demo').")
    return 0


# ---------------------------------------------------------------------------
# report / advise / html / status
# ---------------------------------------------------------------------------

def _title(cfg, args) -> str:
    scope = args.label or args.dest or "all routes"
    bits = [f"{cfg.home} → {scope}"]
    if args.pattern:
        bits.append(args.pattern.replace("_", " "))
    if args.month:
        bits.append(datetime.strptime(args.month, "%Y-%m").strftime("%B %Y"))
    return " · ".join(bits)


def cmd_report(args) -> int:
    cfg = _load(args)
    conn = _db(cfg)

    if args.cheapest:
        rows = report_mod.cheapest(conn, n=args.cheapest, window_days=args.window)
        print(report_mod.render_cheapest(rows, window_days=args.window))
        return 0

    rows = report_mod.grid(
        conn, dest=args.dest, label=args.label, pattern=args.pattern, month=args.month,
        window_days=args.window, include_unpriced=args.include_unpriced, sort=args.sort,
    )
    title = _title(cfg, args)
    if args.sparkline:
        print(report_mod.render_sparklines(conn, rows, f"{title} — price history"))
    else:
        print(report_mod.render_grid(rows, title, window_days=args.window))
    return 0


def cmd_advise(args) -> int:
    cfg = _load(args)
    conn = _db(cfg)
    items = report_mod.advise(
        conn, dest=args.dest, label=args.label, pattern=args.pattern, month=args.month,
        window_days=args.window, route_window_days=cfg.alerts.percentile_window_days,
        targets=expand_mod.trip_targets(cfg),
    )
    print(report_mod.render_advice(items, _title(cfg, args) + " — book or wait?"))
    return 0


def cmd_html(args) -> int:
    cfg = _load(args)
    conn = _db(cfg)
    path = html_mod.write(conn, args.out or cfg.html_path, window_days=args.window, cfg=cfg)
    print(f"[html] wrote {path}")
    return 0


def cmd_status(args) -> int:
    cfg = _load(args)
    conn = _db(cfg)

    active = conn.execute(
        "SELECT COUNT(*) FROM queries q JOIN routes r ON r.id = q.route_id WHERE q.active = 1 AND r.active = 1"
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
    try:
        size_mb = Path(cfg.db_path).stat().st_size / 1e6
        print(f"database       : {cfg.db_path} ({size_mb:.1f} MB)")
    except OSError:
        print(f"database       : {cfg.db_path}")
    until = fetch_mod.cooldown_active(conn)
    if until:
        print(f"cooldown       : ACTIVE until {until}")
    print()

    print(f"{'RUN':>4}  {'KIND':<8}{'STARTED':<21}{'ATT':>4}{'OK':>4}{'FAIL':>5}{'BLOCK':>6}  NOTES")
    rows = conn.execute("SELECT * FROM run_log ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()
    if not rows:
        print("  no runs recorded yet — has cron fired?")
    for r in rows:
        flag = "YES" if r["blocked"] else ("skip" if r["skipped"] else "")
        print(
            f"{r['id']:>4}  {r['kind']:<8}{r['started_at']:<21}{r['attempted']:>4}{r['succeeded']:>4}"
            f"{r['failed']:>5}{flag:>6}  {r['notes'] or ''}"
        )

    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    att = conn.execute(
        "SELECT outcome, COUNT(*) AS n, AVG(latency_ms) AS ms FROM fetch_attempts WHERE at >= ? GROUP BY outcome ORDER BY n DESC",
        (since,),
    ).fetchall()
    if att:
        print("\nlast 7 days of requests:")
        for a in att:
            print(f"  {a['outcome']:<8} {a['n']:>5}   avg {a['ms'] or 0:,.0f} ms")

    print()
    rep = health_mod.check(conn, cfg)
    print(rep.render())
    print()
    return 0 if rep.ok else 1


# ---------------------------------------------------------------------------
# doctor — verify the data path on the host that has network access
# ---------------------------------------------------------------------------

def cmd_doctor(args) -> int:
    print("\n=== flighttrack doctor ===\n")
    ok = True

    try:
        cfg = load(args.config, strict_secrets=False)
        print(f"[ok]   config          : {len(cfg.routes)} routes, {len(cfg.patterns)} patterns, {len(cfg.trips)} trips")
    except ConfigError as exc:
        print(f"[FAIL] config          : {exc}")
        return 1

    channel = (cfg.notify or {}).get("channel", "none")
    if not cfg.alerts.enabled:
        print("[warn] notifications   : alerts are disabled in config")
    elif channel == "ntfy" and not (cfg.notify.get("ntfy") or {}).get("topic"):
        print("[warn] notifications   : ntfy selected but no topic — set $NTFY_TOPIC before `flighttrack alert`")
    else:
        print(f"[ok]   notifications   : {channel}")

    # transports and optional sources
    from .gflights.transport import default_transport

    try:
        transport = default_transport(args.transport or cfg.fetch.transport)
        print(f"[ok]   transport       : {transport.name}" + ("" if transport.name == "primp" else "  (install `primp` for browser TLS fingerprinting)"))
    except Exception as exc:
        print(f"[FAIL] transport       : {exc}")
        return 1
    try:
        import fast_flights  # noqa: F401
        from importlib.metadata import version

        print(f"[ok]   fast-flights    : {version('fast-flights')} (fallback source available)")
        have_ff = True
    except Exception:
        print("[info] fast-flights    : not installed (optional fallback; `pip install 'flighttrack[fallback]'`)")
        have_ff = False

    # offline query construction, cross-checked against fast-flights when present
    from .source import build_query, deep_link

    route = cfg.route(args.dest) if args.dest else cfg.routes[0]
    if route is None:
        print(f"[FAIL] --dest {args.dest} is not a configured route")
        return 1
    depart, ret = args.depart, args.ret
    q = build_query(cfg, route, depart, ret)
    print(f"[ok]   query build     : {q.describe()} — tfs {len(q.tfs())} chars")
    print(f"       deep link       : {deep_link(cfg, route, depart, ret)}")
    if have_ff:
        from fast_flights import FlightQuery, Passengers, create_query

        legs = [FlightQuery(date=depart, from_airport=cfg.home, to_airport=route.dest, max_stops=route.max_stops)]
        if ret:
            legs.append(FlightQuery(date=ret, from_airport=route.dest, to_airport=cfg.home, max_stops=route.max_stops))
        theirs = create_query(flights=legs, seat=cfg.search.seat, trip="round-trip" if ret else "one-way",
                              passengers=Passengers(adults=cfg.search.adults), currency=cfg.search.currency,
                              carry_on_bags=cfg.search.carry_on_bags, exclude_basic_economy=cfg.search.exclude_basic_economy,
                              hide_separate_and_self_transfer=cfg.search.hide_separate_and_self_transfer).to_bytes()
        same = theirs == q.encode()
        print(f"[{'ok' if same else 'FAIL'}]   tfs cross-check : {'byte-identical to fast-flights' if same else 'DIFFERS from fast-flights — report this'}")
        ok &= same

    if args.offline:
        print("\n--offline given: skipping live calls.\n")
        return 0 if ok else 1

    # --- live: our own client ------------------------------------------------
    from .source import FailureStore, GoogleHtmlSource, LayoutError, NoResults, SourceError

    print(f"\n--- live fetch ({cfg.home}→{route.dest} {depart}{' / ' + ret if ret else ''}) via google_html/{transport.name} ---")
    src = GoogleHtmlSource(transport=transport, failures=FailureStore(cfg.failure_dir, cfg.fetch.keep_failure_artifacts))
    t0 = time.monotonic()
    offers = []
    try:
        offers = src.fetch(cfg, route, depart, ret, 10)
        print(f"[ok]   live fetch      : {len(offers)} itineraries in {time.monotonic() - t0:.1f}s")
    except NoResults as exc:
        print(f"[warn] live fetch      : reached Google, no itineraries ({exc}) — try a nearer date with --depart")
    except SourceError as exc:
        print(f"[FAIL] live fetch      : {exc.kind}: {exc}")
        ok = False
    page = src.last_page
    if page is not None:
        print(f"       page            : {page.kind.value} — {page.detail} (HTTP {page.status}, {page.size:,} bytes)")
    if args.dump and src.last_response is not None:
        Path(args.dump).write_text(src.last_response.text, encoding="utf-8")
        print(f"       dumped          : {args.dump}")

    if offers:
        from .source import price_is_plausible

        print("\n--- prices (confirm the dollar amounts against google.com/travel/flights) ---")
        for o in offers[:5]:
            print(f"  raw={o.raw.get('price_raw')!r} → {o.price_cents} cents = ${o.price_cents/100:,.2f}   "
                  f"{o.airline or '?'}  stops={o.stops}  {o.duration_min or '?'} min  [{o.raw.get('bucket')}]   plausible={price_is_plausible(o.price_cents)}")
        buckets = {o.raw.get("bucket") for o in offers}
        if "other" not in buckets:
            print("  note: only the 'best' list was found; the cheapest fare usually sits in 'other'. Check the page dump.")
        if not all(price_is_plausible(o.price_cents) for o in offers):
            ok = False
            print("\n  ⚠ Some prices are outside the plausible band — units may have changed upstream.")

    # fallback comparison when the primary failed on layout
    if have_ff and not offers and page is not None and page.kind.value == "layout":
        from .source import FastFlightsSource

        print("\n--- fallback: fast-flights on the same query ---")
        try:
            fo = FastFlightsSource().fetch(cfg, route, depart, ret, 5)
            print(f"[ok]   fast-flights    : {len(fo)} itineraries, cheapest ${fo[0].price_cents/100:,.0f} — our parser is what broke; see {cfg.failure_dir}")
        except Exception as exc:
            print(f"[FAIL] fast-flights    : {exc} — both parsers fail; Google's page changed for everyone")

    # --- live: calendar RPC (experimental) ------------------------------------
    if args.calendar:
        print(f"\n--- calendar RPC (EXPERIMENTAL) {cfg.home}→{route.dest} ---")
        from .config import Pattern
        from .gflights.transport import TransportError

        pat = cfg.patterns[0] if cfg.patterns else Pattern("doctor", "FRI", 3)
        start = date.fromisoformat(depart)
        job = sweep_mod.SweepJob(route, pat, start.isoformat(), (start + timedelta(days=30)).isoformat())
        try:
            prices, resp = sweep_mod.fetch_window(transport, cfg, job)
            print(f"       response        : HTTP {resp.status}, {resp.size:,} bytes")
            if args.dump:
                Path(args.dump + ".calendar.txt").write_text(resp.text, encoding="utf-8")
                print(f"       dumped          : {args.dump}.calendar.txt")
            if prices:
                print(f"[ok]   calendar        : {len(prices)} dated prices; first: " +
                      ", ".join(f"{p.depart}{'→' + p.ret if p.ret else ''} ${p.price:,.0f}" for p in prices[:5]))
                print("       if these match the price graph on google.com/travel/flights, set calendar.enabled: true")
            else:
                ok = False
                print("[FAIL] calendar        : no (date, price) tuples found — do not enable; save the dump and inspect")
        except TransportError as exc:
            ok = False
            print(f"[FAIL] calendar        : network: {exc}")

    print("\n" + ("All checks passed." if ok else "Some checks FAILED — see above.") + "\n")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# calibrate — measure the rate limit; do not assume it
# ---------------------------------------------------------------------------

def cmd_calibrate(args) -> int:
    cfg = load(args.config, strict_secrets=False)
    if not args.yes:
        print(f"About to issue up to {args.count} real requests to Google Flights at {args.interval}s intervals.\n"
              f"Re-run with --yes to proceed.")
        return 2

    from .source import NoResults, SourceError

    route = cfg.routes[0]
    source = _source(cfg)
    base = datetime.strptime(args.depart, "%Y-%m-%d").date()
    results: list[tuple[int, str, float]] = []
    first_failure_at: int | None = None
    print(f"\n[calibrate] {args.count} requests @ {args.interval}s, {cfg.home}→{route.dest} via {getattr(source, 'name', '?')}\n")

    for i in range(args.count):
        if i:
            time.sleep(args.interval)
        depart = (base + timedelta(days=i)).isoformat()
        ret = (base + timedelta(days=i + 4)).isoformat()
        t0 = time.monotonic()
        try:
            offers = source.fetch(cfg, route, depart, ret, 1)
            status = f"ok (${offers[0].price_cents/100:,.0f})"
        except NoResults as exc:
            status = f"empty: {exc}"
        except SourceError as exc:
            status = f"FAIL[{exc.kind}]: {exc}"
            if first_failure_at is None:
                first_failure_at = i + 1
        dt = time.monotonic() - t0
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
        print(f"[calibrate] FIRST FAILURE at request #{first_failure_at} — set fetch.max_queries_per_run well below it")
    else:
        print(f"[calibrate] no failures at {args.interval}s spacing across {len(results)} requests")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("n,status,seconds\n" + "".join(f"{n},{s},{d:.2f}\n" for n, s, d in results))
        print(f"[calibrate] wrote {out}")
    return 0


# ---------------------------------------------------------------------------
# ingest — observations from any other tool, one JSON object per line
# ---------------------------------------------------------------------------

def cmd_ingest(args) -> int:
    """Record prices found by something else (a browser session, another API).

    Each line: {"dest": "HND", "depart": "2027-04-10", "return": "2027-04-24",
                "price": 812, "airline": "ANA", "stops": 0, "source": "expedia"}
    `price` is in whole currency units. Unknown date pairs get a query row
    with pattern 'manual' so they appear in reports and alerts.
    """
    from .source import Offer, normalize_price

    cfg = _load(args)
    conn = _db(cfg)
    stream = open(args.file, encoding="utf-8") if args.file else sys.stdin
    written = skipped = 0
    for n, line in enumerate(stream, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
            dest = str(rec["dest"]).upper()
            route = cfg.route(dest)
            if route is None:
                raise ValueError(f"{dest} is not a configured route")
            depart = str(rec["depart"])
            ret = rec.get("return")
            cents = normalize_price(rec["price"])
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            print(f"  line {n}: skipped ({exc})", file=sys.stderr)
            skipped += 1
            continue
        row = conn.execute("SELECT id FROM routes WHERE origin = ? AND destination = ?", (cfg.home, dest)).fetchone()
        if row is None:
            expand_mod.sync_routes(conn, cfg)
            row = conn.execute("SELECT id FROM routes WHERE origin = ? AND destination = ?", (cfg.home, dest)).fetchone()
        conn.execute(
            "INSERT INTO queries (route_id, depart_date, return_date, pattern, active) VALUES (?, ?, ?, 'manual', 1) "
            "ON CONFLICT(route_id, depart_date, return_date) DO NOTHING",
            (row["id"], depart, ret),
        )
        qid = conn.execute(
            "SELECT id FROM queries WHERE route_id = ? AND depart_date = ? AND return_date IS ?", (row["id"], depart, ret)
        ).fetchone()["id"]
        offer = Offer(price_cents=cents, currency=str(rec.get("currency", cfg.search.currency)), airline=rec.get("airline"),
                      stops=rec.get("stops"), duration_min=rec.get("duration_min"), source=str(rec.get("source", "manual")),
                      raw={"ingested": True, **rec})
        w, _ = fetch_mod.store(conn, qid, [offer])
        conn.execute("UPDATE queries SET last_fetch_at = ? WHERE id = ?", (fetch_mod.utcnow(), qid))
        conn.commit()
        written += w
    print(f"[ingest] {written} observation(s) written, {skipped} line(s) skipped")
    return 0 if not skipped else 1


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="flighttrack", description="Self-hosted daily flight price sensor.")
    p.add_argument("--config", default=DEFAULT_CONFIG, help=f"config file (default {DEFAULT_CONFIG})")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("expand", help="materialise date patterns and trips into the queries table")
    e.add_argument("--no-links", action="store_true", help="skip deep-link construction")
    e.set_defaults(func=cmd_expand)

    f = sub.add_parser("fetch", help="fetch due queries and append observations")
    f.add_argument("--limit", type=int, default=None, help="override max queries this run")
    f.add_argument("--dry-run", action="store_true", help="show what would be fetched")
    f.add_argument("--no-html", action="store_true", help="skip regenerating the HTML report")
    f.add_argument("--ignore-cooldown", action="store_true", help="run even if a previous run was blocked (not recommended)")
    f.set_defaults(func=cmd_fetch)

    sw = sub.add_parser("sweep", help="EXPERIMENTAL calendar sweep: one RPC per route/pattern window")
    sw.add_argument("--dry-run", action="store_true")
    sw.add_argument("--force", action="store_true", help="run even if calendar.enabled is false")
    sw.add_argument("--no-html", action="store_true")
    sw.set_defaults(func=cmd_sweep)

    a = sub.add_parser("alert", help="evaluate fresh observations and notify")
    a.add_argument("--dry-run", action="store_true", help="print instead of sending")
    a.set_defaults(func=cmd_alert)

    dl = sub.add_parser("deals", help="poll deal feeds and notify on matches")
    dl.add_argument("--dry-run", action="store_true")
    dl.add_argument("--test-match", metavar="TITLE", help="check a headline against your watches, no network")
    dl.set_defaults(func=cmd_deals)

    hl = sub.add_parser("health", help="is the collector working? exit 1 if not")
    hl.add_argument("--notify", action="store_true", help="send a notification if unhealthy (deduped)")
    hl.add_argument("--dry-run", action="store_true")
    hl.set_defaults(func=cmd_health)

    rn = sub.add_parser("run", help="the daily job: expand, sweep, fetch, deals, alert, health, html")
    rn.add_argument("--limit", type=int, default=None)
    rn.add_argument("--dry-run", action="store_true", help="print alerts instead of sending them")
    rn.set_defaults(func=cmd_run)

    sv = sub.add_parser("serve", help="run the daily job on a schedule AND serve the report (one process)")
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--at", default="03:15", help="local time HH:MM for the daily run")
    sv.add_argument("--jitter", type=int, default=45, help="random delay after --at, minutes")
    sv.add_argument("--run-on-start", action="store_true", help="run once immediately, then on schedule")
    sv.add_argument("--limit", type=int, default=None)
    sv.add_argument("--dry-run", action="store_true")
    sv.set_defaults(func=cmd_serve)

    cp = sub.add_parser("compact", help="prune runner-up offers and old logs, then VACUUM (dry run unless --yes)")
    cp.add_argument("--yes", action="store_true")
    cp.set_defaults(func=cmd_compact)

    dm = sub.add_parser("demo", help="seed synthetic history and render the report (no network)")
    dm.add_argument("--db", default="data/demo.db")
    dm.add_argument("--out", default="out/demo.html")
    dm.add_argument("--days", type=int, default=45)
    dm.add_argument("--force", action="store_true")
    dm.set_defaults(func=cmd_demo)

    r = sub.add_parser("report", help="the date-grid table and other slices")
    r.add_argument("--dest", help="filter to one destination airport, e.g. HND")
    r.add_argument("--label", help="filter to one label, e.g. Tokyo (covers HND+NRT)")
    r.add_argument("--pattern", help="filter to one pattern or trip name")
    r.add_argument("--month", help="filter departures to YYYY-MM")
    r.add_argument("--window", type=int, default=30, help="trailing-low window in days")
    r.add_argument("--sort", choices=["date", "price"], default="date")
    r.add_argument("--cheapest", type=int, metavar="N", help="N best current fares across all routes")
    r.add_argument("--sparkline", action="store_true", help="show price history per date pair")
    r.add_argument("--include-unpriced", action="store_true", help="also show never-fetched pairs")
    r.set_defaults(func=cmd_report)

    ad = sub.add_parser("advise", help="book-or-wait verdicts from the history")
    ad.add_argument("--dest")
    ad.add_argument("--label")
    ad.add_argument("--pattern", help="pattern or trip name")
    ad.add_argument("--month")
    ad.add_argument("--window", type=int, default=30)
    ad.set_defaults(func=cmd_advise)

    h = sub.add_parser("html", help="regenerate the static HTML report")
    h.add_argument("--out", help="output path (default from config)")
    h.add_argument("--window", type=int, default=30)
    h.set_defaults(func=cmd_html)

    s = sub.add_parser("status", help="run history and health")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_status)

    d = sub.add_parser("doctor", help="verify the data path end to end")
    d.add_argument("--depart", default=None, help="departure date YYYY-MM-DD")
    d.add_argument("--ret", default=None, help="return date YYYY-MM-DD")
    d.add_argument("--dest", default=None, help="route to test (default: first configured)")
    d.add_argument("--offline", action="store_true", help="skip live calls")
    d.add_argument("--calendar", action="store_true", help="also test the experimental calendar RPC")
    d.add_argument("--transport", choices=["auto", "primp", "urllib"], default=None)
    d.add_argument("--dump", metavar="PATH", help="save the raw response(s) here")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("calibrate", help="measure the rate limit")
    c.add_argument("--count", type=int, default=20)
    c.add_argument("--interval", type=float, default=6.0)
    c.add_argument("--depart", default=None)
    c.add_argument("--stop-on-failure", action="store_true")
    c.add_argument("--out", default=None, help="write per-request CSV here")
    c.add_argument("--yes", action="store_true", help="required: confirms real requests")
    c.set_defaults(func=cmd_calibrate)

    ig = sub.add_parser("ingest", help="record observations from another tool (JSON lines)")
    ig.add_argument("--file", help="read from this file instead of stdin")
    ig.set_defaults(func=cmd_ingest)

    return p


def _default_dates(args) -> None:
    if getattr(args, "depart", None) is None:
        args.depart = (date.today() + timedelta(days=60)).isoformat()
    if getattr(args, "ret", None) is None and args.cmd == "doctor":
        args.ret = (datetime.strptime(args.depart, "%Y-%m-%d").date() + timedelta(days=4)).isoformat()


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
