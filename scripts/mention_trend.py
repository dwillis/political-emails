"""Weekly trend in how often disclaimer committees mention tracked people.

Answers "are Republican committees mentioning Trump less lately?" while
separating *behavior* (the same committees mentioning him less) from
*composition* (new senders who never mentioned him diluting the total).

The build feeds every record through ``add_record`` during its single archive
scan, then ``build_payload`` turns the tallies into ``docs/mention_trend.json``
for the client-rendered ``mention_trend.html`` page. Everything here is pure
(no file I/O) so it can be unit-tested on synthetic records.
"""

import math
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from committee_utils import committee_group_key

PARTIES = ("R", "D")
RECENT_WINDOWS = (6, 7, 8)
BASELINE_WEEKS = 14
MIN_EMAILS = 10
FLAT_BAND_PTS = 5.0

_DISCLAIMER_START_RE = re.compile(r"\bpaid\s+for\s+(?:by|and)\b", re.IGNORECASE)
_FOOTER_START_RE = re.compile(
    r"\b(?:unsubscribe|email\s+preferences|privacy\s+policy|"
    r"this\s+(?:email|message)\s+was\s+sent\s+to)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"\w+")


def mention_text(rec):
    """Return the subject and campaign copy eligible for person matching.

    ``clean_body`` removes most markup. Explicitly remove the disclaimer and
    remaining footer so a name in legal text cannot count as a campaign mention.
    """
    body = str(rec.get("clean_body") or rec.get("body") or "")
    starts = [m.start() for pattern in (_DISCLAIMER_START_RE, _FOOTER_START_RE)
              if (m := pattern.search(body))]
    if starts:
        body = body[:min(starts)]
    return f"{rec.get('subject', '')} {body}"


def week_start(date_key):
    """Return the Monday that starts the calendar week containing date_key."""
    day = date.fromisoformat(date_key)
    return (day - timedelta(days=day.weekday())).isoformat()


# --------------------------------------------------------------------------
# Tallying
# --------------------------------------------------------------------------

# per (week, party, committee) bucket:
#   [emails, mentions, subject_mentions, mention_count, words]
_EMAILS, _MENTIONS, _SUBJECT, _COUNT, _WORDS = range(5)


def _union_pattern(patterns):
    """One alternation so overlapping patterns ("Donald Trump" / "Trump")
    count a single occurrence once."""
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


def new_tracker(people):
    """Tally state for every tracked person."""
    return {
        "patterns": {slug: _union_pattern(spec.get("patterns", []))
                     for slug, spec in people.items() if spec.get("patterns")},
        "tallies": defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0, 0])),
        "names": defaultdict(Counter),
        "types": {},
    }


def add_record(tracker, rec, date_key, type_of=None):
    """Tally one archive record. Only D/R emails with a disclaimer and an
    identified committee count. ``type_of(rec)`` returns a registry entity
    type (or None) for the committee-type breakdown."""
    party = rec.get("party")
    if party not in PARTIES or not rec.get("disclaimer") or not rec.get("committee"):
        return
    key = committee_group_key(rec)
    if not key:
        return
    tracker["names"][key][str(rec.get("committee_canonical") or rec["committee"]).strip()] += 1
    if key not in tracker["types"] and type_of is not None:
        tracker["types"][key] = type_of(rec)
    week = week_start(date_key)
    text = mention_text(rec)
    subject = str(rec.get("subject") or "")
    words = len(_WORD_RE.findall(text))
    for slug, pattern in tracker["patterns"].items():
        n = sum(1 for _ in pattern.finditer(text))
        b = tracker["tallies"][slug][(week, party, key)]
        b[_EMAILS] += 1
        b[_MENTIONS] += n > 0
        b[_SUBJECT] += bool(pattern.search(subject))
        b[_COUNT] += n
        b[_WORDS] += words


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------

def wilson(k, n, z=1.96):
    """Wilson 95% interval for k successes in n trials, as percentages."""
    if n == 0:
        return (None, None)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(100 * max(0.0, center - half), 1), round(100 * min(1.0, center + half), 1))


def two_proportion_p(k1, n1, k2, n2):
    """Two-sided p-value for a difference in proportions (pooled z-test)."""
    if not n1 or not n2:
        return None
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = (k1 / n1 - k2 / n2) / se
    return math.erfc(abs(z) / math.sqrt(2))


def _pct(k, n):
    return round(100 * k / n, 1) if n else None


def _median(values):
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return round(s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2, 1)


# --------------------------------------------------------------------------
# Windows and summaries
# --------------------------------------------------------------------------

def last_complete_week(last_date_key):
    """Monday of the latest week fully covered by the archive (Mon-Sun)."""
    day = date.fromisoformat(last_date_key)
    monday = day - timedelta(days=day.weekday())
    if day.weekday() != 6:
        monday -= timedelta(days=7)
    return monday.isoformat()


def windows(last_week, recent_weeks, baseline_weeks=BASELINE_WEEKS):
    """(baseline_weeks_list, recent_weeks_list) of Monday ISO dates ending at
    last_week, oldest first."""
    end = date.fromisoformat(last_week)
    recent = [(end - timedelta(weeks=i)).isoformat() for i in range(recent_weeks)][::-1]
    first_recent = end - timedelta(weeks=recent_weeks - 1)
    baseline = [(first_recent - timedelta(weeks=i)).isoformat()
                for i in range(1, baseline_weeks + 1)][::-1]
    return baseline, recent


def _sum(buckets):
    out = [0, 0, 0, 0, 0]
    for b in buckets:
        for i, v in enumerate(b):
            out[i] += v
    return out


def _measure(t):
    emails, mentions, subject, count, words = t
    lo, hi = wilson(mentions, emails)
    return {
        "emails": emails,
        "mentions": mentions,
        "share": _pct(mentions, emails),
        "ci": [lo, hi],
        "subject_share": _pct(subject, emails),
        "per_1k_words": round(1000 * count / words, 2) if words else None,
    }


def _row(label, base_buckets, recent_buckets):
    b, r = _sum(base_buckets), _sum(recent_buckets)
    p = two_proportion_p(b[_MENTIONS], b[_EMAILS], r[_MENTIONS], r[_EMAILS])
    mb, mr = _measure(b), _measure(r)
    change = (round(mr["share"] - mb["share"], 1)
              if mb["share"] is not None and mr["share"] is not None else None)
    return {"label": label, "baseline": mb, "recent": mr, "change_pts": change,
            "p_value": None if p is None else float(f"{p:.3g}")}


def is_affiliated(name, pattern):
    """A committee named for the person (e.g. Trump National Committee JFC)."""
    return bool(pattern.search(name or ""))


def summarize(tracker, slug, party, last_week, recent_weeks,
              baseline_weeks=BASELINE_WEEKS, min_emails=MIN_EMAILS, person_name=None):
    """Baseline-vs-recent comparison for one person and party."""
    base_weeks, recent_wk = windows(last_week, recent_weeks, baseline_weeks)
    base_set, recent_set = set(base_weeks), set(recent_wk)
    pattern = tracker["patterns"][slug]
    names = {k: min(v.items(), key=lambda kv: (-kv[1], kv[0]))[0]
             for k, v in tracker["names"].items()}

    base_by = defaultdict(list)
    recent_by = defaultdict(list)
    for (week, p, key), b in tracker["tallies"][slug].items():
        if p != party:
            continue
        if week in base_set:
            base_by[key].append(b)
        elif week in recent_set:
            recent_by[key].append(b)

    def pick(by, keep):
        return [b for k, bs in by.items() if keep(k) for b in bs]

    affiliated = {k for k in set(base_by) | set(recent_by)
                  if is_affiliated(names.get(k, k), pattern)}
    types = tracker["types"]
    rows = [
        _row("All committees", pick(base_by, lambda k: True), pick(recent_by, lambda k: True)),
        _row(f"Excluding committees named for {person_name or slug}",
             pick(base_by, lambda k: k not in affiliated),
             pick(recent_by, lambda k: k not in affiliated)),
    ]
    type_labels = [("federal-candidate", "Candidate committees"),
                   ("federal-pac", "PACs"),
                   ("joint-fundraising", "Joint fundraising committees"),
                   ("party-committee", "National party committees")]
    for t, label in type_labels:
        bb = pick(base_by, lambda k, t=t: types.get(k) == t)
        rb = pick(recent_by, lambda k, t=t: types.get(k) == t)
        if bb or rb:
            rows.append(_row(label, bb, rb))

    # Composition: continuing committees (both windows) vs exits vs entrants.
    continuing = set(base_by) & set(recent_by)
    entrants = set(recent_by) - set(base_by)
    exits = set(base_by) - set(recent_by)
    decomposition = {
        "continuing": {"committees": len(continuing),
                       "baseline": _measure(_sum(pick(base_by, lambda k: k in continuing))),
                       "recent": _measure(_sum(pick(recent_by, lambda k: k in continuing)))},
        "exited": {"committees": len(exits),
                   "baseline": _measure(_sum(pick(base_by, lambda k: k in exits)))},
        "new": {"committees": len(entrants),
                "recent": _measure(_sum(pick(recent_by, lambda k: k in entrants)))},
    }

    # Panel: committees with enough email in BOTH windows.
    panel = []
    for key in continuing:
        b, r = _sum(base_by[key]), _sum(recent_by[key])
        if b[_EMAILS] < min_emails or r[_EMAILS] < min_emails:
            continue
        bs, rs = 100 * b[_MENTIONS] / b[_EMAILS], 100 * r[_MENTIONS] / r[_EMAILS]
        panel.append({
            "committee": names.get(key, key),
            "type": types.get(key),
            "affiliated": key in affiliated,
            "baseline_emails": b[_EMAILS], "baseline_share": round(bs, 1),
            "recent_emails": r[_EMAILS], "recent_share": round(rs, 1),
            "change_pts": round(rs - bs, 1),
        })
    panel.sort(key=lambda x: (x["change_pts"], -x["recent_emails"], x["committee"]))
    counts = {
        "fell": sum(x["change_pts"] < -FLAT_BAND_PTS for x in panel),
        "flat": sum(abs(x["change_pts"]) <= FLAT_BAND_PTS for x in panel),
        "rose": sum(x["change_pts"] > FLAT_BAND_PTS for x in panel),
    }

    def committee_median(by):
        shares = []
        for bs in by.values():
            t = _sum(bs)
            if t[_EMAILS] >= min_emails:
                shares.append(100 * t[_MENTIONS] / t[_EMAILS])
        return {"committees": len(shares), "median_share": _median(shares)}

    return {
        "recent_weeks": recent_weeks,
        "baseline_weeks": baseline_weeks,
        "baseline_range": [base_weeks[0], _week_end(base_weeks[-1])],
        "recent_range": [recent_wk[0], _week_end(recent_wk[-1])],
        "rows": rows,
        "committee_weighted": {"baseline": committee_median(base_by),
                               "recent": committee_median(recent_by)},
        "decomposition": decomposition,
        "panel": panel,
        "panel_counts": counts,
    }


def _week_end(monday):
    return (date.fromisoformat(monday) + timedelta(days=6)).isoformat()


def weekly_series(tracker, slug, party):
    """Full-history weekly totals as compact rows:
    [week, emails, mentions, subject_mentions, mention_count, words,
     committees, affiliated_emails, affiliated_mentions]."""
    pattern = tracker["patterns"][slug]
    names = tracker["names"]
    weeks = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0, 0])
    affiliated_cache = {}
    for (week, p, key), b in tracker["tallies"][slug].items():
        if p != party:
            continue
        w = weeks[week]
        for i in range(5):
            w[i] += b[i]
        w[5] += 1
        if key not in affiliated_cache:
            name = names[key].most_common(1)[0][0] if names.get(key) else key
            affiliated_cache[key] = is_affiliated(name, pattern)
        if affiliated_cache[key]:
            w[6] += b[_EMAILS]
            w[7] += b[_MENTIONS]
    return [[week, *vals] for week, vals in sorted(weeks.items())]


def build_payload(tracker, people, last_date_key, generated_at=None,
                  recent_windows=RECENT_WINDOWS, baseline_weeks=BASELINE_WEEKS,
                  min_emails=MIN_EMAILS):
    """JSON-ready payload for docs/mention_trend.json."""
    last_week = last_complete_week(last_date_key) if last_date_key else None
    out = {
        "generated_at": generated_at,
        "last_complete_week": last_week,
        "baseline_weeks": baseline_weeks,
        "recent_windows": list(recent_windows),
        "min_emails": min_emails,
        "flat_band_pts": FLAT_BAND_PTS,
        "weekly_columns": ["week", "emails", "mentions", "subject_mentions",
                           "mention_count", "words", "committees",
                           "affiliated_emails", "affiliated_mentions"],
        "people": {},
    }
    for slug in tracker["patterns"]:
        entry = {"name": people[slug].get("name", slug), "weekly": {}, "summaries": {}}
        for party in PARTIES:
            entry["weekly"][party] = weekly_series(tracker, slug, party)
            if last_week:
                entry["summaries"][party] = {
                    str(n): summarize(tracker, slug, party, last_week, n,
                                      baseline_weeks, min_emails, entry["name"])
                    for n in recent_windows
                }
        out["people"][slug] = entry
    return out


def registry_type_lookup(registry):
    """Return a ``type_of(rec)`` function backed by the committee registry:
    FEC id first, then the canonical display name."""
    by_fec = {e["fec_id"]: e.get("type") for e in registry.values() if e.get("fec_id")}
    by_name = {str(e["name"]).casefold(): e.get("type") for e in registry.values()}

    def type_of(rec):
        fec = rec.get("committee_fec_id")
        if fec and fec in by_fec:
            return by_fec[fec]
        canonical = rec.get("committee_canonical")
        if canonical:
            return by_name.get(str(canonical).casefold())
        return None

    return type_of
