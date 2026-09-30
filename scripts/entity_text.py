"""Evidence-preserving text for entity extraction, independent of site previews.

Offsets refer to the returned text. The archived body is never changed. Link
labels survive, and subscription notices do not truncate subsequent copy.
"""
import re

VERSION = 'entity-text-v2'
CONTEXTS = ['campaign', 'signature', 'payment', 'legal', 'recipient']
LINK = re.compile(r'!?\[([^\]]*)\]\((?:[^()\s]|\([^()]*\))*\)')
URL = re.compile(r'https?://\S+')
# Narrow repairs for observed HTML-to-text joins; do not split arbitrary CamelCase
# (McCarthy, ActBlue, etc.). Every repair is recorded in input_repairs.
JOIN = re.compile(
    r'(?<=[a-z])(?=(?:Attorney General|Candidate for|U\.S\. Senator|'
    r'[A-Z][a-z]+[’\']s \d+(?:st|nd|rd|th) (?:Congressional )?District|'
    r'P\.S\.|Team [A-Z]|SECURE(?=\s|\d)|RSVP\b))'
    r'|(?<=District)(?=[A-Z][a-z]+ for (?:Congress|Senate))'
    r'|(?<=Hall)(?=\d{1,5}\b)'
    r'|(?<=\d[ap]m)(?=[A-Z]{2,})'
    # Addresses: require a country name or city followed by a state abbreviation.
    r'|(?<=\d)(?=(?:United States\b|[A-Z][a-z]+(?: [A-Z][a-z]+)*,\s*[A-Z]{2}\b))'
    r'|(?:(?<=Blvd)|(?<=Ave)|(?<=Rd)|(?<=St)|(?<=SE)|(?<=NE)|(?<=NW)|(?<=SW))'
    r'(?=[A-Z][a-z]+(?: [A-Z][a-z]+)*,\s*[A-Z]{2}\b)'
    # A joined article and club name followed by a verb, not arbitrary TheBrand.
    r'|(?<=\bThe)(?=[A-Z][^\n.!?]{5,100}Club(?:had to|has|will)\b)'
    r'|(?<=Club)(?=(?:had to|has|will)\b)'
)
LEGAL = re.compile(r'\b(?:paid for (?:by|and)|contributions? (?:to .{0,100} )?are not (?:tax[ -])?deductible|federal income tax|mail checks to|privacy policy|unsubscribe|email preferences|this (?:email|message) was sent to)\b', re.I)
PAYMENT = re.compile(r'(?:saved|stored).{0,60}payment (?:information|info)|(?:donation|contribution).{0,60}(?:process|go through) immediately', re.I)
SIGNATURE = re.compile(r'^\s*(?:thank you[,!]?|thanks[,!]?|sincerely[,!]?|with gratitude[,!]?|gratefully[,!]?|onward[,!]?|in friendship[,!]?|[—–])\s*', re.I)


def prepare_text(rec):
    body = str(rec.get('body') or rec.get('clean_body') or '')
    body = LINK.sub(lambda m: '' if m.group(0).startswith('!') else m.group(1), body)
    body = URL.sub('', body)
    body = re.sub(r'(?m)^\s*(?:[-*_]{3,}|(?:\|\s*)+)\s*$', '', body)
    body = re.sub(r'[*_`#]', '', body)
    body = body.replace('\r\n', '\n').replace('\r', '\n')
    repairs = [{'position_before_repair': m.start(), 'context': body[max(0,m.start()-30):m.start()+40], 'inserted': '\n'} for m in JOIN.finditer(body)]
    body = JOIN.sub('\n', body)
    body = re.sub(r'[ \t]+', ' ', body)
    body = re.sub(r'\n{3,}', '\n\n', body).strip()
    fields = {'subject': str(rec.get('subject') or ''), 'campaign_body': body}
    return fields, repairs


def mention_context(field, text, start, end, proposed='campaign'):
    """Conservative line-local overrides; never let a footer poison later copy."""
    if field == 'subject':
        return proposed
    line_start = text.rfind('\n', 0, start)+1
    line = text[line_start:text.find('\n', end) if '\n' in text[end:] else len(text)]
    relative = start-line_start
    # Notices later in a long paragraph must not relabel earlier campaign copy.
    if any(m.start() <= relative for m in PAYMENT.finditer(line)):
        return 'payment'
    if any(m.start() <= relative for m in LEGAL.finditer(line)):
        return 'legal'
    if SIGNATURE.search(line):
        return 'signature'
    return proposed


def recipient_span(text, start, end):
    """Recognize forms of direct address, not a global blacklist of names."""
    name = text[start:end]
    if len(name.split()) != 1:
        return False
    before, after = text[max(0,start-45):start], text[end:end+55]
    greeting = bool(re.search(r'(?:^|[\n|])\s*(?:(?:Hi|Hey|Dear|Hello)\s+)?$', before, re.I) and re.match(r'\s*[,–—]', after))
    address = bool(re.search(r'\b(?:you|your support|supporters like you|so much|together|repetitive|campaign|reading this|minute|afford|out)\W*$', before, re.I) and re.match(r'\s*[,!.—–]', after))
    petition_form = bool(re.search(r'add your name|sign(?:ing)? (?:our|the|this) letter|sign.{0,20}petition', text, re.I) and re.search(r'(?m)^FROM:\s*' + re.escape(name) + r'\s*$', text, re.I) and re.search(r'(?m)^X\s+' + re.escape(name) + r'\s*$', text))
    form_field = bool(re.search(r'(?:^|\n)(?:FROM:|X)\s*$', before, re.I))
    return greeting or address or (petition_form and form_field)
