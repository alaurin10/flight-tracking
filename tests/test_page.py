"""The report page: sections, escaping, both skeleton modes, and (optionally) its JS."""

import os
from pathlib import Path

import pytest

from flighttrack import demo, html
from flighttrack.config import Trip
from flighttrack.db import connect
from helpers import make_config, make_db


def _demo(tmp_path):
    cfg = make_config(trips=[Trip("japan", "HND", "2027-04-10", "2027-04-24", target_price=80000)],
                      max_days_ahead=90, notify={"channel": "none"})
    conn = connect(tmp_path / "demo.db")
    demo.seed(conn, cfg, days=20)
    return conn, cfg


def test_demo_seed_populates_everything(tmp_path):
    conn, cfg = _demo(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE source = 'demo' AND is_best = 1").fetchone()[0] > 100
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE source = 'google_calendar'").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0] >= 14
    assert conn.execute("SELECT COUNT(*) FROM deal_posts WHERE watch IS NOT NULL").fetchone()[0] == 3


def test_page_has_every_section_and_trip_target(tmp_path):
    conn, cfg = _demo(tmp_path)
    page = html.render(conn, cfg=cfg)
    for marker in ("Your trips", "Best in each grid", "Date grids", "Announced deals", "Collector", "synthetic demo data",
                   "Open in Google Flights", 'class="cell', "data-points=", "target $800", "Tokyo · HND"):
        assert marker in page, marker
    assert page.startswith("<!DOCTYPE html>") and "prefers-color-scheme: dark" in page and 'data-theme="dark"' in page
    frag = html.render(conn, cfg=cfg, fragment=True)
    assert frag.startswith("<title>") and "<html" not in frag and "<body" not in frag


def test_page_escapes_hostile_strings(tmp_path):
    conn, cfg = make_db(tmp_path, make_config())
    conn.execute("UPDATE routes SET label = '<script>alert(1)</script>'")
    conn.execute("INSERT INTO deal_posts (guid, feed, title, link, seen_at, price_cents, watch) VALUES ('g','f','<img src=x onerror=alert(1)>','javascript:alert(1)','2026-01-01T00:00:00Z',100,'<b>w</b>')")
    conn.commit()
    page = html.render(conn, cfg=cfg)
    assert "<script>alert(1)</script>" not in page and "<img src=x" not in page and "<b>w</b>" not in page
    assert "&lt;script&gt;" in page


@pytest.mark.skipif(not os.environ.get("FLIGHTTRACK_BROWSER_TESTS"), reason="set FLIGHTTRACK_BROWSER_TESTS=1 with playwright installed")
def test_page_javascript_in_a_real_browser(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    conn, cfg = _demo(tmp_path)
    out = html.write(conn, tmp_path / "index.html", cfg=cfg)
    exe = next((p for p in Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")), None)
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=str(exe)) if exe else p.chromium.launch()
        pg = b.new_page(viewport={"width": 1100, "height": 800})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(out.as_uri())
        assert pg.locator(".bar i.ok").first.bounding_box()["height"] > 0        # bars have height
        pg.click("[data-toggle]")
        assert pg.locator(".gridcard .tw").first.is_visible()                       # table twin toggles
        pg.click('.chip[data-filter="Tokyo"]')
        assert pg.locator('.gridcard[data-label="Salt Lake City"]').first.is_hidden()
        pg.hover(".chart svg")
        assert pg.locator(".chart .tip").first.is_visible()                         # crosshair readout
        assert errors == []
        b.close()
