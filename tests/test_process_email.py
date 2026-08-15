"""Tests for process_email HTML extraction (used by screenshotting)."""

from email.message import EmailMessage

from process_email import determine_party, extract_html


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


# --- determine_party -> (party, source) ---

DMAP = {"foo.com": "D", "bar.org": "R"}


def test_party_empty_body():
    assert determine_party("", "foo.com", DMAP) == (None, None)


def test_party_domain_map_case_insensitive():
    assert determine_party("hi", "FOO.com", DMAP) == ("D", "domain-map")


def test_party_platform_from_urls():
    urls = ["https://secure.actblue.com/x", "https://actblue.com/y", "https://winred.com/z"]
    assert determine_party("body", "unknown.com", DMAP, urls) == ("D", "platform")


def test_party_platform_body_fallback():
    assert determine_party("donate at winred.com now", "unknown.com", DMAP) == ("R", "platform")


def test_party_platform_tie_is_none():
    urls = ["https://actblue.com/a", "https://winred.com/b"]
    assert determine_party("body", "unknown.com", DMAP, urls) == (None, None)


def test_party_anedot_not_republican():
    assert determine_party("give via anedot.com", "unknown.com", DMAP) == (None, None)


def test_party_no_signal():
    assert determine_party("just text", "unknown.com", DMAP) == (None, None)
