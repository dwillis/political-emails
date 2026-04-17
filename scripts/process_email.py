"""Email processing module — extracts and cleans political fundraising email data.

Refactored from ddhq_code/python/mbox_json.py into a reusable module.
Takes a single email.message.Message and returns a structured dict.
"""

import email.utils
import email.header
import hashlib
from datetime import datetime
import html2text
import csv
import json
import re
import unicodedata


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FOOTER_MARKERS = [
    r'unsubscribe',
    r'email\s+preferences',
    r'manage\s+(your\s+)?subscriptions?',
    r'opt[\s-]?out',
    r'update\s+(your\s+)?preferences',
    r'view\s+(this\s+)?(email|message)\s+in\s+(your\s+)?browser',
    r'forward\s+to\s+a\s+friend',
    r'privacy\s+policy',
    r'terms\s+(of\s+service|and\s+conditions)',
    r'you\s+are\s+receiving\s+this',
    r'this\s+(email|message)\s+was\s+sent\s+(to|by)',
    r'to\s+stop\s+receiving',
    r'if\s+you\s+no\s+longer\s+wish',
    r'click\s+here\s+to\s+unsubscribe',
]
_FOOTER_RE = re.compile(
    r'^\s*(' + '|'.join(FOOTER_MARKERS) + r')',
    re.IGNORECASE | re.MULTILINE,
)

_PAID_FOR_RE = re.compile(
    r'(paid\s+for\s+(?:by|and)\s+.{3,120}?)(?:\.|$|\n)',
    re.IGNORECASE,
)

# Markdown artifact patterns produced by html2text
_MD_IMAGE_RE   = re.compile(r'!\[[^\]]*\]\([^)]*\)')
_MD_LINK_RE    = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')
_MD_BOLD_RE    = re.compile(r'\*{1,2}([^*]+)\*{1,2}')
_MD_UNDER_RE   = re.compile(r'_{1,2}([^_]+)_{1,2}')
_MD_HEADING_RE = re.compile(r'^#{1,6}\s+', re.MULTILINE)
_MD_HRULE_RE   = re.compile(r'^[-*_]{3,}\s*$', re.MULTILINE)
_MD_BQUOTE_RE  = re.compile(r'^>\s?', re.MULTILINE)

_URL_RE = re.compile(
    r'https?://'
    r'[A-Za-z0-9](?:[A-Za-z0-9._~:/?#\[\]@!$&\'()*+,;=%-]*[A-Za-z0-9/])?',
    re.ASCII,
)

_JUNK_CHARS = str.maketrans('', '', (
    '\u200b\u200c\u200d\u200e\u200f'
    '\u2060\u2061\u2062\u2063\u2064'
    '\ufeff\u00ad\u034f\u061c'
    '\ufffe\uffff'
))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def decode_subject(subject):
    """Decode RFC 2047 encoded subject lines."""
    if not subject:
        return ""
    subject_str = str(subject)
    try:
        decoded_parts = email.header.decode_header(subject_str)
        decoded_subject = ""
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                decoded_subject += part.decode(encoding or 'utf-8', errors='ignore')
            else:
                decoded_subject += str(part)
        return ' '.join(decoded_subject.split())
    except Exception:
        return ' '.join(subject_str.split())


def normalize_unicode(text):
    """Apply NFKC normalization and strip invisible / formatting characters."""
    if not text:
        return text
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'[\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000]', ' ', text)
    text = text.translate(_JUNK_CHARS)
    return text


def strip_markdown_artifacts(text):
    """Remove markdown formatting left by html2text, preserving URLs and readable text."""
    if not text:
        return text
    text = _MD_IMAGE_RE.sub('', text)
    text = _MD_LINK_RE.sub(r'\2', text)
    text = _MD_BOLD_RE.sub(r'\1', text)
    text = _MD_UNDER_RE.sub(r'\1', text)
    text = _MD_HEADING_RE.sub('', text)
    text = _MD_HRULE_RE.sub('', text)
    text = _MD_BQUOTE_RE.sub('', text)
    return text


def strip_boilerplate(text):
    """Remove common email footer / boilerplate below first footer marker.

    Returns (cleaned_text, paid_for_line).
    """
    if not text:
        return text, ''
    paid_for_match = _PAID_FOR_RE.search(text)
    paid_for_line = paid_for_match.group(1).strip() if paid_for_match else ''
    match = _FOOTER_RE.search(text)
    if match:
        text = text[:match.start()].rstrip()
    return text, paid_for_line


def normalize_whitespace(text):
    """Normalise whitespace while preserving paragraph breaks."""
    if not text:
        return text
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)
    lines = text.split('\n')
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in lines]
    cleaned = []
    for i, line in enumerate(lines):
        if line == '':
            if cleaned and cleaned[-1] != '':
                cleaned.append('')
        else:
            cleaned.append(line)
    text = '\n'.join(cleaned).strip()
    return text


# ---------------------------------------------------------------------------
# Body extraction
# ---------------------------------------------------------------------------

def _make_html2text():
    """Create a consistently-configured html2text converter."""
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.ignore_emphasis = False
    h.body_width = 0
    h.ignore_tables = False
    h.default_image_alt = ""
    h.pad_tables = False
    h.single_line_break = True
    return h


def extract_body_content(message):
    """Extract and clean body content from email message.

    Returns (body, clean_body) where:
      - body: lightly cleaned text preserving paragraph structure
      - clean_body: aggressively cleaned text (no markdown, no boilerplate)
    """
    plain_text_content = ""
    html_content = ""

    def get_safe_charset(part_or_message):
        charset = part_or_message.get_content_charset()
        if not charset or charset.lower() in ['text/html', 'text/plain', 'html', 'plain']:
            return 'utf-8'
        charset = charset.lower()
        return {
            'iso-8859-1': 'latin-1',
            'windows-1252': 'cp1252',
            'us-ascii': 'ascii',
        }.get(charset, charset)

    def safe_decode_payload(part_or_message):
        try:
            payload = part_or_message.get_payload(decode=True)
            if not payload:
                return ""
            charset = get_safe_charset(part_or_message)
            try:
                return payload.decode(charset, errors='ignore')
            except (UnicodeDecodeError, LookupError):
                for fb in ['utf-8', 'latin-1', 'cp1252', 'ascii']:
                    try:
                        return payload.decode(fb, errors='ignore')
                    except (UnicodeDecodeError, LookupError):
                        continue
                return payload.decode('utf-8', errors='replace')
        except Exception:
            return ""

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == 'multipart':
                continue
            content_disposition = part.get_content_disposition()
            if content_disposition and content_disposition.lower() == 'attachment':
                continue
            content_type = part.get_content_type()
            try:
                if content_type == "text/plain":
                    text_content = safe_decode_payload(part)
                    if text_content:
                        plain_text_content += text_content + "\n"
                elif content_type == "text/html":
                    html_part = safe_decode_payload(part)
                    if html_part:
                        html_content += html_part + "\n"
            except Exception:
                continue
    else:
        content_type = message.get_content_type()
        try:
            payload = safe_decode_payload(message)
            if content_type == "text/plain":
                plain_text_content = payload
            elif content_type == "text/html":
                html_content = payload
        except Exception:
            return "", ""

    html_as_text = ""
    if html_content:
        try:
            html_as_text = _make_html2text().handle(html_content)
        except Exception:
            import html as html_mod
            unescaped = html_mod.unescape(html_content)
            html_as_text = re.sub(r'<[^>]+>', ' ', unescaped)

    if plain_text_content and html_as_text:
        final_body = html_as_text if len(html_as_text) > len(plain_text_content) else plain_text_content
    elif plain_text_content:
        final_body = plain_text_content
    elif html_as_text:
        final_body = html_as_text
    else:
        return "", ""

    final_body = normalize_unicode(final_body)
    final_body = normalize_whitespace(final_body)
    body = final_body

    clean_body = strip_markdown_artifacts(final_body)
    clean_body, _ = strip_boilerplate(clean_body)
    clean_body = _URL_RE.sub('', clean_body)
    clean_body = re.sub(r'\s+', ' ', clean_body).strip()

    return body, clean_body


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def determine_party(body, origin_domain, domain_party_map):
    """Determine political party based on domain mapping first, then body heuristics."""
    if not body:
        return None
    if origin_domain and origin_domain.strip() in domain_party_map:
        return domain_party_map[origin_domain.strip()]
    body_lower = body.lower()
    if 'actblue.com' in body_lower or 'ngpvan.com' in body_lower:
        return 'D'
    if 'winred.com' in body_lower or 'anedot.com' in body_lower:
        return 'R'
    return None


def has_disclaimer(body):
    """Check if email body contains a political disclaimer."""
    if not body:
        return False
    body_lower = body.lower()
    return "paid for by" in body_lower or "paid for and" in body_lower


def extract_disclaimer_text(body):
    """Extract the full 'Paid for by ...' line from the body, or ''."""
    if not body:
        return ''
    m = _PAID_FOR_RE.search(body)
    return m.group(1).strip() if m else ''


def extract_urls(body):
    """Extract unique URLs from email body."""
    if not body:
        return []
    urls = _URL_RE.findall(body)
    cleaned = []
    for url in urls:
        url = url.rstrip('.,;:!?\'")')
        if url:
            cleaned.append(url)
    return sorted(set(cleaned))


# ---------------------------------------------------------------------------
# Domain-party mapping loader
# ---------------------------------------------------------------------------

def load_domain_party_map(path):
    """Load domain -> party mapping from CSV file."""
    domain_party_map = {}
    with open(path, "r") as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        for row in reader:
            domain, party = row
            domain_party_map[domain] = party
    return domain_party_map


# ---------------------------------------------------------------------------
# Unique ID generation
# ---------------------------------------------------------------------------

def generate_unique_id(message_id, sender_email, subject, date_str, body):
    """Generate a unique ID for an email using SHA-256.

    Uses message_id when present; falls back to a hash of the body.
    Combined with sender_email, subject, and date for the final hash.
    """
    if message_id:
        id_component = message_id
    else:
        # No Message-ID — use body hash as fallback
        id_component = hashlib.sha256((body or '').encode('utf-8')).hexdigest()

    raw = f"{id_component}|{sender_email}|{subject}|{date_str}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# Main entry point: process a single email message
# ---------------------------------------------------------------------------

def process_single_email(message, domain_party_map):
    """Process a single email.message.Message into a structured record dict.

    Returns a dict with all fields, or None if the message can't be processed.
    """
    message_id = message.get('Message-ID', '')
    if message_id:
        message_id = message_id.strip()

    from_header = str(message.get('From', ''))
    sender_name, sender_email = email.utils.parseaddr(from_header)
    subject = decode_subject(message.get('Subject', ''))

    # Parse date
    date_header = str(message.get('Date', ''))
    parsed_date = None
    if date_header:
        try:
            parsed_date = email.utils.parsedate_to_datetime(date_header)
        except (ValueError, TypeError):
            parsed_date = None

    # Extract domain
    origin_domain = ''
    if sender_email and '@' in str(sender_email):
        origin_domain = str(sender_email).split('@')[1]

    # Body extraction
    body = ''
    clean_body = ''
    party = None
    disclaimer = False
    disclaimer_text = ''
    urls = []

    try:
        body, clean_body = extract_body_content(message)
        if body:
            urls = extract_urls(body)
            party = determine_party(body, origin_domain, domain_party_map)
            disclaimer = has_disclaimer(body)
            disclaimer_text = extract_disclaimer_text(body)
    except Exception:
        pass

    # Generate unique ID
    unique_id = generate_unique_id(
        message_id,
        str(sender_email) if sender_email else '',
        str(subject) if subject else '',
        parsed_date.isoformat() if parsed_date else '',
        body,
    )

    # Build record with proper types (not strings)
    record = {
        'unique_id': unique_id,
        'message_id': message_id,
        'name': str(sender_name) if sender_name else '',
        'email': str(sender_email) if sender_email else '',
        'subject': str(subject) if subject else '',
        'domain': str(origin_domain) if origin_domain else '',
        'party': party,
        'disclaimer': disclaimer,
        'disclaimer_text': disclaimer_text,
    }

    if parsed_date:
        record.update({
            'date': parsed_date.isoformat(),
            'year': parsed_date.year,
            'month': parsed_date.month,
            'day': parsed_date.day,
            'hour': parsed_date.hour,
            'minute': parsed_date.minute,
        })
    else:
        record.update({
            'date': None,
            'year': None,
            'month': None,
            'day': None,
            'hour': None,
            'minute': None,
        })

    record['body'] = body
    record['clean_body'] = clean_body
    record['urls'] = urls

    return record


def content_key(record):
    """Generate a content-level dedup key from a record (sender+subject+date)."""
    email_addr = (record.get('email') or '').lower()
    subj = (record.get('subject') or '').lower()
    date_str = ''
    if record.get('date'):
        # Use just the date portion for comparison
        date_str = record['date'][:10]
    return (email_addr, subj, date_str)
