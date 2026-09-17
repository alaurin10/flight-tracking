"""Page classification: the four outcomes the fetcher must tell apart."""

from flighttrack.gflights.page import PageKind, classify, extract_ds1
from flighttrack.gflights.transport import Response
from helpers import NO_RESULTS_HTML, make_itinerary, make_payload, make_results_html


def _resp(text, status=200, url="https://www.google.com/travel/flights/search?tfs=x"):
    return Response(status=status, url=url, text=text, elapsed=0.1, transport="fake", headers={})


def test_results_page_is_parsed():
    page = classify(_resp(make_results_html(make_payload(best=[make_itinerary(811)]))))
    assert page.kind is PageKind.RESULTS and page.ok
    assert page.payload[3][0][0][1][0][1] == 811
    assert "script.ds:1" in page.signals


def test_results_found_via_callback_fallback_when_script_class_is_gone():
    html = make_results_html(make_payload(best=[make_itinerary(500)]), wrap="bare")
    _, hits = extract_ds1(html)
    assert hits == ["AF_initDataCallback"]
    assert classify(_resp(html)).kind is PageKind.RESULTS


def test_no_results_marker():
    assert classify(_resp(NO_RESULTS_HTML)).kind is PageKind.NO_RESULTS


def test_blocked_by_status_and_by_text_and_by_url():
    assert classify(_resp("<html>rate limited</html>", status=429)).kind is PageKind.BLOCKED
    assert classify(_resp("<html><title>Sorry</title>Our systems have detected unusual traffic from your computer network</html>")).kind is PageKind.BLOCKED
    assert classify(_resp("<html/>", url="https://www.google.com/sorry/index?continue=x")).kind is PageKind.BLOCKED


def test_data_blob_wins_over_scary_words():
    """A results page mentioning recaptcha in a script must never be a 'block'."""
    html = make_results_html(make_payload(best=[make_itinerary(700)])).replace("</body>", "<script>recaptcha</script></body>")
    assert classify(_resp(html)).kind is PageKind.RESULTS


def test_consent_interstitial():
    assert classify(_resp("<html/>", url="https://consent.google.com/m?continue=x")).kind is PageKind.CONSENT
    assert classify(_resp("<html><h1>Before you continue to Google</h1></html>")).kind is PageKind.CONSENT


def test_server_error_and_layout_change():
    assert classify(_resp("<html><title>Error 503</title></html>", status=503)).kind is PageKind.ERROR_STATUS
    page = classify(_resp("<html><title>Google Flights</title><body>new shiny app shell</body></html>"))
    assert page.kind is PageKind.LAYOUT_UNKNOWN
    assert "Google Flights" in page.detail


def test_undecodable_blob_is_layout_not_crash():
    html = "<script class=\"ds:1\">AF_initDataCallback({key: 'ds:1', data:[1,2,, sideChannel: {}});</script>"
    assert classify(_resp(html)).kind is PageKind.LAYOUT_UNKNOWN
