"""GEPA-optimize the LLM fallback prompt against the gold set.

Only the ~25% of records where the deterministic disclaimer parse fails reach
the LLM fallback, so we optimize that predictor alone, on exactly those gold
rows. GEPA reflects on failures using textual feedback that enforces the
disclaimer-priority rule (the observed failure mode is models answering from the
sender's name instead of the disclaimer).

    uv run --group enrich python scripts/optimize_fallback.py --auto light

Writes config/fallback_optimized.json when the optimized program beats the
baseline on the held-out val split. IdentifyCommitteeModule loads that file
automatically when present.

Needs Ollama for the task model and a (stronger) reflection model.
"""

import argparse
import random

import dspy

from committee_extract import extract_committee, looks_confident
from committee_utils import norm_label
from eval_committees import build_archive_index, download_gold, gold_key, GOLD_PATH
from enrich_committees import configure_dspy
from identify_committee import FALLBACK_INSTRUCTIONS
from utils import DATA_DIR

OPTIMIZED_PATH = DATA_DIR.parent / "config" / "fallback_optimized.json"


class FallbackProgram(dspy.Module):
    """Standalone wrapper around the fallback predictor so GEPA can compile it."""

    def __init__(self):
        super().__init__()
        self.predict = dspy.Predict(
            dspy.Signature("email_body: str -> committee: str", FALLBACK_INSTRUCTIONS)
        )

    def forward(self, email_body):
        return self.predict(email_body=email_body)


def metric(gold, pred, trace=None, pred_name=None, pred_trace=None):
    """Score + textual feedback (GEPA reflects on the feedback)."""
    correct = norm_label(getattr(pred, "committee", "")) == norm_label(gold.committee)
    if correct:
        return dspy.Prediction(score=1.0, feedback="Correct.")
    fb = (
        f"Predicted {getattr(pred, 'committee', None)!r} but the correct committee is "
        f"{gold.committee!r}. When a disclaimer ('Paid for by ...') is present, the "
        "committee MUST be taken from the disclaimer text -- never from the sender's "
        "name, signature, or 'from' line."
    )
    return dspy.Prediction(score=0.0, feedback=fb)


def build_examples():
    """Gold rows where the deterministic parse fails -> the fallback's domain."""
    download_gold()
    import csv
    with open(GOLD_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    index = build_archive_index()
    examples = []
    for row in rows:
        k = gold_key(row)
        rec = index.get(k) if k else None
        if rec is None:
            continue
        body = rec.get("body") or ""
        if looks_confident(extract_committee(body)):
            continue  # deterministic path handles it; not a fallback case
        examples.append(
            dspy.Example(email_body=body, committee=row["committee"].strip())
            .with_inputs("email_body")
        )
    return examples


def accuracy(program, examples):
    hits = 0
    for ex in examples:
        try:
            pred = program(email_body=ex.email_body)
            hits += norm_label(pred.committee) == norm_label(ex.committee)
        except Exception:  # noqa: BLE001
            pass
    return hits / len(examples) if examples else 0.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:4b", help="Task model (Ollama)")
    parser.add_argument("--reflection-model", default="deepseek-v4-flash:cloud")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--auto", default="light", choices=["light", "medium", "heavy"])
    parser.add_argument("--val-frac", type=float, default=0.4)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    configure_dspy(args.model, args.ollama_url, disable_thinking=True)

    examples = build_examples()
    print(f"Fallback-relevant gold examples: {len(examples)}")
    if len(examples) < 10:
        print("Too few examples to optimize meaningfully; aborting.")
        return
    rng = random.Random(args.seed)
    rng.shuffle(examples)
    cut = int(len(examples) * (1 - args.val_frac))
    train, val = examples[:cut], examples[cut:]
    print(f"train={len(train)} val={len(val)}")

    student = FallbackProgram()
    base = accuracy(student, val)
    print(f"baseline val accuracy: {base:.3f}")

    reflection_lm = dspy.LM(f"ollama_chat/{args.reflection_model}", api_base=args.ollama_url)
    gepa = dspy.GEPA(metric=metric, auto=args.auto, reflection_lm=reflection_lm)
    optimized = gepa.compile(student, trainset=train, valset=val)

    opt = accuracy(optimized, val)
    print(f"optimized val accuracy: {opt:.3f}  (baseline {base:.3f})")
    if opt > base:
        OPTIMIZED_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Save just the inner predictor so IdentifyCommitteeModule.fallback (a
        # dspy.Predict) can load it directly.
        optimized.predict.save(str(OPTIMIZED_PATH))
        print(f"saved -> {OPTIMIZED_PATH}")
    else:
        print("optimized program did not beat baseline; not saving.")


if __name__ == "__main__":
    main()
