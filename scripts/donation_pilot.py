"""Local donation-page pilot: select emails, capture evidence, extract, report.

No source archive writes, donation actions, or inferred allocation percentages.
Firecrawl CLI must be installed/authenticated for the fetch stage only.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

from committee_utils import normalize_committee
from utils import DATA_DIR, STATE_DIR

VERSION = 'donation-pilot-v1'
ASK = re.compile(r'\b(donat\w*|contribut\w*|chip in|pitch in|give|split|support)\b|\$\d', re.I)
EXCLUDE = re.compile(r'unsubscribe|preferences|privacy|opt.?out|view.{0,8}browser', re.I)
LINK = re.compile(r'\[([^\]\n]*)\]\((https?://[^\s)]+)\)')
PLATFORMS = ('actblue.com', 'winred.com', 'anedot.com')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write('\n')
        tmp = Path(f.name)
    tmp.replace(path)


def host(url):
    try:
        parsed = urlsplit(url)
        return (parsed.hostname or '').lower() if parsed.scheme in ('http', 'https') else ''
    except ValueError:
        return ''


def platform(url):
    hostname = host(url)
    return next((p for p in PLATFORMS if hostname == p or hostname.endswith('.' + p)), None)


def donation_links(record):
    body = record.get('body') or ''
    anchors = {}
    for match in LINK.finditer(body):
        url = html.unescape(match[2])
        anchors.setdefault(url, []).append({'anchor': match[1], 'context': body[max(0, match.start()-150):match.end()+150]})
    links = []
    for url in sorted(set(record.get('urls') or []) | set(anchors)):
        url = html.unescape(url)
        if not host(url):
            continue
        occurrences = anchors.get(url, [])
        anchor = ' '.join(o['anchor'] for o in occurrences)
        if EXCLUDE.search(anchor) or EXCLUDE.search(urlsplit(url).path):
            continue
        direct = bool(platform(url) and re.search(r'/(donate|contribute|contribution|[\w-]+/donate)(?:/|$)', urlsplit(url).path))
        contextual = bool(ASK.search(anchor))
        path_hint = bool(re.search(r'/(donate|contribute)(?:/|$)', urlsplit(url).path, re.I))
        if direct or contextual or path_hint:
            links.append({'url': url, 'url_id': digest(url), 'selection_reason': 'platform' if direct else 'anchor' if contextual else 'path', 'occurrences': occurrences})
    return list({link['url']: link for link in links}.values())


def select(data_dir, limit=100, per_committee=5, since=None, until=None):
    emails, committees = [], Counter()
    counts = Counter()
    seen = set()
    for path in sorted(data_dir.glob('*/*/*.jsonl'), reverse=True):
        if since and path.stem < since or until and path.stem > until:
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            counts['scanned'] += 1
            committee = normalize_committee(rec.get('committee'))
            if not rec.get('disclaimer') or not committee:
                continue
            counts['eligible'] += 1
            uid = rec.get('unique_id')
            if not uid:
                counts['missing_id'] += 1
                continue
            if uid in seen:
                continue
            seen.add(uid)
            links = donation_links(rec)
            if not links:
                counts['eligible_without_detected_links'] += 1
                continue
            key = rec.get('committee_fec_id') or rec.get('committee_canonical') or committee
            if committees[key] >= per_committee:
                counts['committee_cap_skipped'] += 1
                continue
            committees[key] += 1
            emails.append({'email_id': uid, 'date': rec.get('date'), 'subject': rec.get('subject'), 'committee': committee, 'committee_id': rec.get('committee_fec_id'), 'committee_source': rec.get('committee_source'), 'source_file': str(path), 'source_hash': digest(line), 'links': links})
            if len(emails) >= limit:
                return {'version': VERSION, 'selected_at': now(), 'sampling': 'latest first, committee cap; not representative', 'counts': dict(counts), 'emails': emails}
    return {'version': VERSION, 'selected_at': now(), 'sampling': 'latest first, committee cap; not representative', 'counts': dict(counts), 'emails': emails}


def embedded_actblue(raw):
    match = re.search(r'window\.indigoListResponse\s*=\s*', raw)
    if not match:
        return None
    try:
        return json.JSONDecoder().raw_decode(raw[match.end():])[0]
    except (ValueError, TypeError):
        return None


def extract_details(document, url):
    """Parse observed platform structure; retain generic evidence for review."""
    raw = document.get('rawHtml') or document.get('html') or ''
    markdown = document.get('markdown') or ''
    recipients = []
    payload = embedded_actblue(raw) if platform(url) == 'actblue.com' else None
    # Find the entities list in the platform's parsed form payload, never execute JS.
    def walk(value, path='$'):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'entities' and isinstance(child, list):
                    for entity in child:
                        if isinstance(entity, dict) and entity.get('display_name'):
                            recipients.append({'name_as_written': entity['display_name'], 'platform_entity_id': str(entity.get('id')) if entity.get('id') is not None else None, 'platform': 'actblue.com', 'kind': entity.get('kind'), 'location': entity.get('display_pretty_location'), 'committee_id': None, 'resolution_status': 'unresolved', 'evidence_source': path + '.entities', 'evidence': entity})
                else:
                    walk(child, path + '.' + key)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f'{path}[{i}]')
    if payload:
        walk(payload)
    evidence = []
    for line in markdown.splitlines():
        if re.search(r'will benefit|proceeds|allocated|allocation|split|divided|paid for by|\b\d+(?:\.\d+)?%', line, re.I):
            evidence.append(line)
    # Only use the explicit single-recipient sentence as fallback, not page title.
    if not recipients:
        match = re.search(r'Your contribution will benefit ([^\n]+)', markdown)
        if match:
            name = re.split(r'\.(?:Candidate|PAC|Party|\s*$)', match[1])[0].strip().rstrip('.')
            if not re.search(r'\band\b|,|\d+ (?:candidates|groups|organizations)', name, re.I) and len(name) < 150:
                recipients.append({'name_as_written': name, 'platform_entity_id': None, 'committee_id': None, 'resolution_status': 'unresolved', 'evidence_source': 'markdown', 'evidence': match[0]})
    unique = {(r.get('platform_entity_id'), r['name_as_written']): r for r in recipients}
    allocation = {'type': 'unknown', 'shares': [], 'donor_adjustable': None}
    equal = re.search(r'Your contribution will be split evenly between ([^\n]+)', markdown)
    if equal and len(unique) > 1:
        names_text = equal[1].replace('**', '').rstrip('.').strip()
        expected = ', '.join(r['name_as_written'] for r in unique.values())
        # Verify the sentence enumerates exactly the extracted recipients.
        normalized = re.sub(r',? and ', ', ', names_text)
        bold_names = re.findall(r'\*\*([^*]+)\*\*', equal[1])
        names_match = len(bold_names) == len(unique) and set(bold_names) == {r['name_as_written'] for r in unique.values()}
        if normalized == expected or names_match:
            allocation = {'type': 'equal', 'shares': [{'platform_entity_id': r.get('platform_entity_id'), 'name': r['name_as_written'], 'percent': 100 / len(unique)} for r in unique.values()], 'donor_adjustable': True if 'Customize amounts' in markdown else None, 'evidence': equal[0], 'basis': 'default displayed allocation; percentages derived from explicit equal split'}
    return {'extractor_version': VERSION, 'status': 'recipients_extracted' if unique else 'needs_review', 'recipients': list(unique.values()), 'allocation': allocation, 'evidence_excerpts': evidence, 'review_status': 'unreviewed', 'external_candidate_solicitation': 'unknown'}


def fetch_page(url, output_dir):
    """Full query strings retained; no donation or form-submission actions."""
    uid = digest(url)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = output_dir / 'snapshots' / f'{uid}-{stamp}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    result = {'url_id': uid, 'original_url': url, 'requested_at': now(), 'status': 'fetch_failed'}
    try:
        proc = subprocess.run(['firecrawl', 'scrape', url, '--format', 'markdown,rawHtml', '--max-age', '0', '--output', str(target)], capture_output=True, text=True, timeout=120)
        if proc.returncode:
            result['error'] = 'Firecrawl exited unsuccessfully; retry explicitly.'
            return result
        document = json.loads(target.read_text())
        document = document.get('data', document)
        metadata = document.get('metadata') or {}
        result.update(snapshot_path=str(target), fetched_at=now(), content_hash=digest(target.read_text()), final_url=metadata.get('url') or metadata.get('sourceURL') or url, final_url_source='provider_metadata', http_status=metadata.get('statusCode'), redirect_chain=None)
        if not (document.get('markdown') or document.get('rawHtml')) or (metadata.get('statusCode') or 200) >= 400:
            result['error'] = 'Empty or HTTP error page; evidence retained.'
            return result
        result['status'] = 'fetched'
        result['details'] = extract_details(document, result['final_url'])
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result['error'] = type(exc).__name__
    return result


def report(manifest, pages):
    selected = manifest['emails']
    ids = {link['url_id'] for email in selected for link in email['links']}
    relevant = [pages[uid] for uid in ids if uid in pages]
    fetched = [p for p in relevant if p['status'] == 'fetched']
    extracted = [p for p in fetched if p['details']['recipients']]
    multi = [p for p in extracted if len(p['details']['recipients']) > 1]
    return {'selected_emails': len(selected), 'selected_committees': len({e['committee_id'] or e['committee'] for e in selected}), 'unique_candidate_urls': len(ids), 'attempted_pages': len(relevant), 'fetched_pages': len(fetched), 'destination_hosts': dict(Counter(host(p.get('final_url', '')) for p in fetched)), 'failed_pages': len(relevant)-len(fetched), 'pending_pages': len(ids)-len(relevant), 'pages_with_recipients': len(extracted), 'pages_with_multiple_recipients': len(multi), 'pages_with_numeric_allocations': sum(bool(p['details']['allocation']['shares']) for p in fetched), 'emails_with_recipient_evidence': sum(any(link['url_id'] in pages and pages[link['url_id']].get('details', {}).get('recipients') for link in e['links']) for e in selected), 'limitation': 'Later page observations, not verified historical recipients. Allocation and candidate identity require review. Link detector is heuristic; selection is not representative.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['select', 'fetch', 'extract', 'report'])
    parser.add_argument('--output-dir', type=Path, default=STATE_DIR / 'donation_pilot')
    parser.add_argument('--data-dir', type=Path, default=DATA_DIR)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--per-committee', type=int, default=5)
    parser.add_argument('--since')
    parser.add_argument('--until')
    parser.add_argument('--max-pages', type=int, default=10)
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    if min(args.limit, args.per_committee, args.max_pages) < 1:
        parser.error('limits must be positive')
    root = args.output_dir.resolve()
    manifest_path = root / 'manifest.json'
    if args.stage == 'select':
        if manifest_path.exists():
            parser.error('manifest already exists; choose a new --output-dir to preserve the sample')
        write_json(manifest_path, select(args.data_dir, args.limit, args.per_committee, args.since, args.until))
    if not manifest_path.exists():
        parser.error('run select first')
    manifest = json.loads(manifest_path.read_text())
    pages_path = root / 'pages.json'
    pages = json.loads(pages_path.read_text()) if pages_path.exists() else {}
    if args.stage == 'fetch':
        # Round-robin across emails so a long first email cannot consume the budget.
        emails = sorted(manifest['emails'], key=lambda e: not any(re.search(r'\bsplit\b', o['context'], re.I) for link in e['links'] for o in link['occurrences']))
        batches = [e['links'] for e in emails]
        ordered = [batch[i] for i in range(max(map(len, batches), default=0)) for batch in batches if i < len(batch)]
        attempts = 0
        attempted = set()
        for link in ordered:
            uid = link['url_id']
            if uid in attempted or uid in pages and not (args.retry_failed and pages[uid]['status'] == 'fetch_failed'):
                continue
            attempted.add(uid)
            pages[uid] = fetch_page(link['url'], root)
            write_json(pages_path, pages)
            attempts += 1
            print(f"Page {attempts}: {pages[uid]['status']}", flush=True)
            if attempts >= args.max_pages:
                break
    if args.stage == 'extract':
        for page in pages.values():
            if page['status'] == 'fetched':
                document = json.loads(Path(page['snapshot_path']).read_text())
                page['details'] = extract_details(document.get('data', document), page['final_url'])
        write_json(pages_path, pages)
    summary = report(manifest, pages)
    write_json(root / 'report.json', summary)
    review = [{'url_id': uid, 'snapshot_path': p.get('snapshot_path'), 'details': p.get('details'), 'status': p['status']} for uid, p in pages.items()]
    write_json(root / 'review.json', review)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
