"""Tests for screenshot_emails target selection and rendering."""

import re

import pytest

from screenshot_emails import select_targets, wrap_plaintext


PATTERN = re.compile(r"\bdata[\s-]?centers?\b", re.IGNORECASE)


def _rec(uid, mid, party, date, subject="", body=""):
    return {
        "unique_id": uid,
        "message_id": mid,
        "party": party,
        "date": date,
        "subject": subject,
        "body": body,
    }


def _records():
    return [
        _rec("u1", "<a>", "D", "2026-01-10T08:00:00+00:00",
             subject="New data center", body="hi"),
        _rec("u2", "<b>", "R", "2026-02-15T08:00:00+00:00",
             body="a datacenter is coming"),
        _rec("u3", "<c>", "D", "2026-03-20T08:00:00+00:00",
             subject="unrelated", body="vote today"),
        _rec("u4", "<d>", None, "2026-04-01T08:00:00+00:00",
             subject="data-centers everywhere"),
    ]


def test_select_targets_matches_pattern():
    hits = select_targets(_records(), PATTERN)
    ids = [r["unique_id"] for r in hits]
    assert ids == ["u1", "u2", "u4"]  # u3 has no match


def test_select_targets_filters_by_party():
    hits = select_targets(_records(), PATTERN, party="R")
    assert [r["unique_id"] for r in hits] == ["u2"]


def test_select_targets_filters_by_date_range():
    hits = select_targets(_records(), PATTERN, since="2026-02-01", until="2026-03-31")
    assert [r["unique_id"] for r in hits] == ["u2"]


def test_select_targets_respects_limit():
    hits = select_targets(_records(), PATTERN, limit=2)
    assert [r["unique_id"] for r in hits] == ["u1", "u2"]


def test_select_targets_limit_zero_means_all():
    hits = select_targets(_records(), PATTERN, limit=0)
    assert len(hits) == 3


def test_select_targets_filters_by_disclaimer():
    recs = _records()
    recs[0]["disclaimer"] = True   # u1
    recs[1]["disclaimer"] = False  # u2
    recs[3]["disclaimer"] = True   # u4
    hits = select_targets(recs, PATTERN, disclaimer=True)
    assert [r["unique_id"] for r in hits] == ["u1", "u4"]


def test_select_targets_disclaimer_false_does_not_filter():
    recs = _records()
    for r in recs:
        r["disclaimer"] = False
    hits = select_targets(recs, PATTERN, disclaimer=False)
    assert [r["unique_id"] for r in hits] == ["u1", "u2", "u4"]


def test_select_targets_skips_records_without_message_id():
    recs = _records()
    recs[0]["message_id"] = ""
    hits = select_targets(recs, PATTERN)
    assert [r["unique_id"] for r in hits] == ["u2", "u4"]


def test_resolve_date_range_defaults_to_current_year():
    from screenshot_emails import resolve_date_range
    assert resolve_date_range(current_year=2026) == ("2026-01-01", "2026-12-31")


def test_resolve_date_range_specific_year():
    from screenshot_emails import resolve_date_range
    assert resolve_date_range(year=2024, current_year=2026) == ("2024-01-01", "2024-12-31")


def test_resolve_date_range_all_years_disables_bounds():
    from screenshot_emails import resolve_date_range
    assert resolve_date_range(all_years=True, current_year=2026) == (None, None)


def test_resolve_date_range_explicit_since_until_override():
    from screenshot_emails import resolve_date_range
    assert resolve_date_range(
        since="2025-03-01", until="2025-04-01", current_year=2026
    ) == ("2025-03-01", "2025-04-01")


def test_resolve_date_range_explicit_since_only_leaves_until_open():
    from screenshot_emails import resolve_date_range
    assert resolve_date_range(since="2025-03-01", current_year=2026) == ("2025-03-01", None)


def test_wrap_plaintext_escapes_and_wraps():
    html = wrap_plaintext("plain <b> & text\nsecond line")
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "&lt;b&gt;" in html  # escaped
    assert "&amp;" in html
    assert "second line" in html


class _FakeMail:
    """Minimal IMAP stub returning one canned raw message for any search."""

    def __init__(self, raw_bytes):
        self._raw = raw_bytes

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b"1"]
        if command == "fetch":
            return "OK", [(b"1 (RFC822 {%d}" % len(self._raw), self._raw)]
        return "NO", [None]


def test_screenshot_email_renders_html_message(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from email.message import EmailMessage
    from screenshot_emails import screenshot_email

    msg = EmailMessage()
    msg["Subject"] = "Test"
    msg["Message-ID"] = "<x@example.org>"
    msg.set_content("plain fallback")
    msg.add_alternative(
        "<html><body style='width:600px'><h1>data center news</h1></body></html>",
        subtype="html",
    )
    rec = {"unique_id": "u1", "message_id": "<x@example.org>",
           "date": "2026-01-10T08:00:00+00:00", "body": "plain fallback"}

    try:
        path = screenshot_email(_FakeMail(msg.as_bytes()), rec, tmp_path)
    except RuntimeError as e:
        pytest.skip(f"Playwright browser unavailable: {e}")

    assert path is not None
    assert path.name == "2026-01-10_u1.png"
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_html_to_png_writes_valid_png(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from screenshot_emails import render_html_to_png

    out = tmp_path / "shot.png"
    html = "<html><body style='width:600px'><h1>data center</h1></body></html>"
    try:
        render_html_to_png(html, out)
    except RuntimeError as e:
        pytest.skip(f"Playwright browser unavailable: {e}")

    assert out.exists()
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_survives_hanging_remote_resource(tmp_path):
    """A tracking pixel / image whose host never responds must not stall the
    render — real fundraising emails routinely reference such resources."""
    pytest.importorskip("playwright.sync_api")
    import socket
    import threading
    import time

    from screenshot_emails import render_html_to_png

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    conns = []

    def _accept_and_hang():
        while True:
            try:
                conn, _ = srv.accept()
                conns.append(conn)  # keep open, never respond
            except OSError:
                break

    threading.Thread(target=_accept_and_hang, daemon=True).start()

    out = tmp_path / "hang.png"
    html = (
        f"<html><body style='width:600px'><h1>data center</h1>"
        f"<img src='http://127.0.0.1:{port}/pixel.png' width='1' height='1'>"
        f"</body></html>"
    )
    try:
        start = time.monotonic()
        render_html_to_png(html, out)
        elapsed = time.monotonic() - start
    except RuntimeError as e:
        if "executable" in str(e).lower() or "install" in str(e).lower():
            pytest.skip(f"Playwright browser unavailable: {e}")
        raise  # a render timeout is a real failure, not a skip
    finally:
        srv.close()
        for c in conns:
            c.close()

    assert out.exists()
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert elapsed < 25  # must not fall back to the 30s networkidle timeout
