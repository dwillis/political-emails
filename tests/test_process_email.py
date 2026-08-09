"""Tests for process_email HTML extraction (used by screenshotting)."""

from email.message import EmailMessage

from process_email import extract_html


def _multipart_email(plain, html):
    msg = EmailMessage()
    msg["Subject"] = "Test"
    msg["From"] = "sender@example.org"
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")
    return msg


def test_extract_html_returns_html_part():
    msg = _multipart_email(
        "plain fallback", "<html><body><h1>Hello data center</h1></body></html>"
    )
    html = extract_html(msg)
    assert "<h1>Hello data center</h1>" in html


def test_extract_html_plaintext_only_returns_empty():
    msg = EmailMessage()
    msg["Subject"] = "Test"
    msg.set_content("just plain text, no html here")
    assert extract_html(msg) == ""


def test_extract_html_single_part_html():
    msg = EmailMessage()
    msg["Subject"] = "Test"
    msg.set_content("<p>inline html</p>", subtype="html")
    assert "<p>inline html</p>" in extract_html(msg)
