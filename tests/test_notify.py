"""The notify seam. Channels must be swappable without touching alert logic."""

import pytest

from flighttrack.notify import NotifyError, notify


def test_none_channel_is_a_noop():
    notify({"channel": "none"}, "s", "b")  # must not raise


def test_unknown_channel_raises():
    with pytest.raises(NotifyError, match="unknown notify channel"):
        notify({"channel": "smoke-signal"}, "s", "b")


def test_file_channel_appends(tmp_path):
    path = tmp_path / "digest.md"
    conf = {"channel": "file", "file": {"path": str(path)}}
    notify(conf, "First", "body one", "https://example.test/a")
    notify(conf, "Second", "body two")

    text = path.read_text()
    assert "First" in text and "Second" in text
    assert "https://example.test/a" in text


def test_ntfy_without_topic_is_an_error():
    with pytest.raises(NotifyError, match="topic is not set"):
        notify({"channel": "ntfy", "ntfy": {"server": "https://ntfy.sh"}}, "s", "b")


def test_ntfy_posts_title_and_click(monkeypatch):
    """Verify the request shape without any network."""
    captured = {}

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    notify(
        {"channel": "ntfy", "ntfy": {"server": "https://ntfy.example/", "topic": "abc"}},
        "Cheap flight",
        "SEA→HND $612",
        "https://google.test/flights?tfs=xyz",
    )

    assert captured["url"] == "https://ntfy.example/abc"
    assert captured["body"] == "SEA→HND $612".encode()
    assert captured["headers"]["title"] == "Cheap flight"
    assert captured["headers"]["click"] == "https://google.test/flights?tfs=xyz"


def test_smtp_requires_host_and_recipient():
    with pytest.raises(NotifyError, match="host and to"):
        notify({"channel": "smtp", "smtp": {"port": 587}}, "s", "b")
