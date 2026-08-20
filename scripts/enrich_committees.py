"""Monthly committee enrichment using the DSPy IdentifyCommitteeModule.

Scans daily JSONL files for records without a committee (null/missing) and runs
each one through scripts/identify_committee.py, which first parses the
"Paid for by ..." disclaimer deterministically and falls back to an LLM
(via DSPy + SiliconFlow's OpenAI-compatible API) only when that fails. Results
are written back to the archive. Intended to be run manually each month --
deliberately NOT in GitHub Actions.

    uv run --group enrich python scripts/enrich_committees.py --month 2026-02
    uv run --group enrich python scripts/enrich_committees.py --since 2026-02-01 --until 2026-02-28

Requires DSPy and a SiliconFlow API key in SILICONFLOW_API_KEY (or --api-key).
Point --api-base at a local Ollama (http://localhost:11434) to use a local model
instead.

Resumability: each day file is rewritten as soon as it finishes, so a crash
loses at most the in-progress day, and re-running skips records already filled.
Note: unknown results are stored as null (same as "never processed"), so
re-running a month retries any records still unresolved.
"""

import argparse
import gc
import os
import resource
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from committee_utils import needs_committee, normalize_committee
from utils import DATA_DIR, load_jsonl, save_jsonl

DEFAULT_MODEL = "Qwen/Qwen3.5-9B"
DEFAULT_API_BASE = "https://api.siliconflow.com/v1"
API_KEY_ENV = "SILICONFLOW_API_KEY"
OLLAMA_URL = "http://localhost:11434"


def raise_fd_limit(target=16384):
    """Raise the open-file soft limit.

    litellm leaks ~1-2 file descriptors per call (a new asyncio event loop
    + httpx pool per request), and macOS's default soft limit is only 256, so a
    long enrichment run hits "Too many open files" after ~150 calls. Don't rely
    on the caller's shell ulimit -- raise it in-process.
    """
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft >= target:
            return
        new_soft = target if hard == resource.RLIM_INFINITY else min(target, hard)
        resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))
    except (ValueError, OSError):
        pass


def is_ollama(api_base):
    """True if the base URL looks like a local Ollama rather than SiliconFlow."""
    return "11434" in api_base or "localhost" in api_base or "127.0.0.1" in api_base


def configure_dspy(model, api_base=DEFAULT_API_BASE, disable_thinking=True, api_key=None):
    """Point DSPy at the LLM used for the module's fallback.

    SiliconFlow (the default) speaks the OpenAI chat API, so it goes through
    litellm's "openai/" provider with an explicit api_base. A localhost/11434
    api_base switches to Ollama instead, which keeps eval_committees.py and
    optimize_fallback.py working against a local model.

    Reasoning ("thinking") models are ~200x slower here and mangle the
    structured output, so thinking is disabled by default: SiliconFlow takes
    `enable_thinking: false` in the request body, Ollama takes `think: false`.
    Pass disable_thinking=False for a plain instruct model that rejects those.
    """
    import dspy

    if is_ollama(api_base):
        kwargs = {"api_base": api_base}
        if disable_thinking:
            kwargs["think"] = False
        lm = dspy.LM(f"ollama_chat/{model}", **kwargs)
    else:
        key = api_key or os.environ.get(API_KEY_ENV)
        if not key:
            sys.exit(f"No API key: set {API_KEY_ENV} or pass --api-key "
                     f"(or use --api-base {OLLAMA_URL} for a local Ollama).")
        kwargs = {"api_base": api_base, "api_key": key}
        if disable_thinking:
            # Qwen3 hybrid-thinking models on SiliconFlow: non-OpenAI params
            # have to ride along in extra_body.
            kwargs["extra_body"] = {"enable_thinking": False}
        lm = dspy.LM(f"openai/{model}", **kwargs)
    dspy.configure(lm=lm)
    raise_fd_limit()


def day_files_for_range(start, end):
    """Yield daily JSONL paths whose YYYY-MM-DD stem falls within [start, end]."""
    for path in sorted(DATA_DIR.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.jsonl")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if start <= day <= end:
            yield path


def resolve_range(args):
    """Return (start_date, end_date) from --month or --since/--until."""
    if args.month:
        year, month = (int(x) for x in args.month.split("-"))
        start = date(year, month, 1)
        first_next = date(year + (month == 12), (month % 12) + 1, 1)
        return start, first_next - timedelta(days=1)
    if args.since and args.until:
        return date.fromisoformat(args.since), date.fromisoformat(args.until)
    # Default: previous calendar month.
    first_this = date.today().replace(day=1)
    end = first_this - timedelta(days=1)
    return end.replace(day=1), end


def is_connection_error(exc):
    """True if the exception looks like the LLM endpoint being unreachable."""
    text = str(exc).lower()
    return any(s in text for s in ("connection", "connect", "refused", "max retries"))


def identify(module, rec):
    """Run one record through the module. Returns (committee_or_None, source).

    source is "disclaimer" when the deterministic parse answered, else "llm".
    Safe to call concurrently: DSPy's own Evaluate/optimizers share a single
    module across a thread pool the same way.
    """
    prediction = module(email_body=rec.get("body") or "")
    return normalize_committee(prediction.committee), getattr(prediction, "source", "llm")


def build_party_deriver():
    """Return a callable (committee, fill_source) -> (party, source), or None.

    Uses FEC (with candidate linkage) + overrides + committee-name keywords, the
    same signals the sweep uses minus majority (which needs a full-tree pass).
    Degrades to None if the FEC cache can't be built.
    """
    try:
        from fec_match import download_fec, load_fec_index, match_name
        from party_utils import derive_committee_party, load_fec_party_map, load_party_overrides
        from utils import CONFIG_DIR
        download_fec()
        name_index, buckets = load_fec_index()
        fec_party_map = load_fec_party_map()
        overrides = load_party_overrides(CONFIG_DIR / "committee_party_overrides.csv")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] party derivation disabled: {e}")
        return None

    cache = {}

    def fec_lookup(norm):
        if norm not in cache:
            mt, fid, _n, _s = match_name(norm, name_index, buckets)
            cache[norm] = fid if mt == "exact" else None
        return cache[norm]

    def derive(committee, fill_source):
        return derive_committee_party(committee, fill_source, fec_lookup, fec_party_map, overrides)

    return derive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_argument_group("date range (default: previous month)")
    group.add_argument("--month", help="YYYY-MM to process")
    group.add_argument("--since", help="YYYY-MM-DD start (inclusive)")
    group.add_argument("--until", help="YYYY-MM-DD end (inclusive)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model id for the LLM fallback")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE,
                        help=f"OpenAI-compatible base URL (default SiliconFlow; use {OLLAMA_URL} for local Ollama)")
    parser.add_argument("--api-key", default=None, help=f"API key (default: ${API_KEY_ENV})")
    parser.add_argument("--limit", type=int, default=None, help="Max records to process (for testing)")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent LLM workers per day (for Ollama, also set OLLAMA_NUM_PARALLEL>1)")
    parser.add_argument("--allow-thinking", action="store_true", help="Don't disable model reasoning (needed for non-thinking instruct models)")
    parser.add_argument("--skip-party", action="store_true", help="Don't derive party when filling committees")
    parser.add_argument("--dry-run", action="store_true", help="Identify but don't write files")
    args = parser.parse_args()

    configure_dspy(args.model, args.api_base, disable_thinking=not args.allow_thinking,
                   api_key=args.api_key)
    # Import after imports so a missing dspy fails inside configure_dspy above.
    from identify_committee import IdentifyCommitteeModule

    module = IdentifyCommitteeModule()
    derive_party = None if args.skip_party else build_party_deriver()
    start, end = resolve_range(args)
    backend = "Ollama" if is_ollama(args.api_base) else "SiliconFlow"
    print(f"Enriching committees for {start} .. {end} (LLM fallback: {backend} '{args.model}')")

    processed = 0
    filled = 0
    unknown = 0
    errors = 0
    party_filled = 0

    for path in day_files_for_range(start, end):
        records = load_jsonl(path)
        pending = [r for r in records if needs_committee(r)]
        if not pending:
            continue

        # Respect the global --limit by trimming this day's batch up front.
        if args.limit is not None:
            remaining = args.limit - processed
            if remaining <= 0:
                break
            pending = pending[:remaining]

        day_changed = False

        def handle(rec, exc=None, result=None):
            """Fold one record's outcome into the running counters.

            result is the (committee_or_None, source) tuple from identify().
            """
            nonlocal processed, filled, unknown, errors, day_changed, party_filled
            processed += 1
            if exc is not None:
                if is_connection_error(exc):
                    sys.exit(f"LLM call failed ({args.api_base}, model '{args.model}'): {exc}\n"
                             "Check network access and the API base URL/model id.")
                print(f"  [error] {path.stem} {rec.get('email')}: {exc}")
                errors += 1
                return
            committee, source = result
            if committee is not None:
                rec["committee"] = committee
                committee_source = "disclaimer" if source == "disclaimer" else f"llm:{args.model}"
                rec["committee_source"] = committee_source
                filled += 1
                day_changed = True
                # Derive party from the newly-known committee (never overrides human).
                if derive_party is not None and rec.get("party_source") != "human":
                    p, psrc = derive_party(committee, committee_source)
                    if p is not None and rec.get("party") != p:
                        rec["party"] = p
                        rec["party_source"] = psrc
                        party_filled += 1
            else:
                unknown += 1

        if args.workers > 1:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(identify, module, rec): rec for rec in pending}
                for future in as_completed(futures):
                    rec = futures[future]
                    try:
                        handle(rec, result=future.result())
                    except SystemExit:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        handle(rec, exc=exc)
        else:
            for rec in pending:
                try:
                    handle(rec, result=identify(module, rec))
                except SystemExit:
                    raise
                except Exception as exc:  # noqa: BLE001
                    handle(rec, exc=exc)

        if day_changed and not args.dry_run:
            save_jsonl(path, records)
        # Reclaim the event-loop/httpx fds litellm leaves behind each day.
        gc.collect()
        print(f"  {path.stem}: {len(pending)} pending, {processed} processed so far")

        if args.limit is not None and processed >= args.limit:
            print(f"Reached --limit {args.limit}; stopping.")
            break

    action = "would fill" if args.dry_run else "filled"
    print(
        f"\nDone. Processed {processed:,} records: {action} {filled:,} committees "
        f"({party_filled:,} party derived), {unknown:,} unresolved (null), {errors:,} errors."
    )


if __name__ == "__main__":
    main()
