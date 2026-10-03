"""Resolve git rebase conflicts in data/**/*.jsonl as a record-level union.

During `git pull --rebase`, "ours" (stage 2) is the upstream branch and
"theirs" (stage 3) is the commit being replayed. Records are keyed by
message_id; upstream wins for shared records. Run backfill_fec_ids.py
afterward to re-enrich any records that only exist on the replayed side.
"""
import json
import subprocess
import sys

from utils import save_jsonl


def stage(path, n):
    r = subprocess.run(["git", "show", f":{n}:{path}"], capture_output=True, text=True)
    if r.returncode != 0:
        return []
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def main():
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    other = [p for p in out if not p.endswith(".jsonl")]
    if other:
        print(f"Unresolvable non-JSONL conflicts: {other}", file=sys.stderr)
        sys.exit(1)
    for path in out:
        merged = {r["message_id"]: r for r in stage(path, 3)}
        merged.update({r["message_id"]: r for r in stage(path, 2)})
        save_jsonl(path, list(merged.values()))
        subprocess.run(["git", "add", path], check=True)
        print(f"Merged {path}: {len(merged)} records")


if __name__ == "__main__":
    main()
