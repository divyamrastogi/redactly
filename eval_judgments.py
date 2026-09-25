"""Offline eval harness for the TypeSafe Jev judgment layer.

Run BEFORE enabling any JEV_* flag in production — it measures, on data you
control, whether the semantic judgments are good enough to change redaction
decisions, and which thresholds to use:

    venv/bin/python eval_judgments.py [--skip-statements]

Part A runs static labeled sets through the real judgment API and reports
accuracy per threshold, so the module defaults (KEYWORD_THRESHOLD,
PERSON_CONFIDENCE) can be tuned on evidence instead of vibes.

Part B re-redacts the real statement fixtures in tests/e2e_statements/ with
the flags off (baseline) vs on, and reports every kept-row flip for manual
review. Fixtures are gitignored; missing ones are skipped.

Requires TYPESAFE_API_KEY in the environment (the feature flags are set
in-process for the duration of the run). Nothing here runs on a server —
this is a local developer tool; statement descriptions it prints are the
same ones the redaction engines already log.

PRIVACY: part A/B send the sample texts to the TypeSafe API for judgment.
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import judgment


# ── Part A: static labeled sets ─────────────────────────────────────────────
# (text, is_person) — drawn from the shapes Presidio actually mis-flags on
# statements (the "hard lesson from benchmarking" in pii_layer.py).
NAME_CASES = [
    ("SHELL GSTATIONS LONDON", False),
    ("TESCO STORES 3487", False),
    ("AMAZON UK MARKETPLACE", False),
    ("NETFLIX.COM 408-555-0138", False),
    ("TFL TRAVEL CHARGE", False),
    ("PRET A MANGER L 281", False),
    ("UBER TRIP HELP.HELP.UBER.COM", False),
    ("MR J SMITH", True),
    ("MISS AOIFE BYRNE", True),
    ("DR DAVID M RASTOGI", True),
    ("MRS S PATEL", True),
]

# (description, categories, expected) — informal-category matching.
KEYWORD_CASES = [
    ("SHELL GSTATIONS LONDON", ["fuel"], True),
    ("BP CONNECT M1 SERVICES", ["fuel"], True),
    ("TESCO FUEL 2341", ["fuel"], True),
    ("TESCO STORES 3487", ["fuel"], False),
    ("TFL TRAVEL CHARGE", ["travel"], True),
    ("UBER TRIP HELP.UBER.COM", ["travel"], True),
    ("AVIOS TRAVEL REWARDS", ["travel"], True),
    ("NETFLIX.COM", ["travel"], False),
    ("NETFLIX.COM", ["entertainment"], True),
    ("SPOTIFY UK", ["entertainment"], True),
    ("VIRGIN MEDIA MEDIA LTD", ["utilities"], True),
    ("THAMES WATER", ["utilities"], True),
    ("LANDLORD RENT MAY", ["rent"], True),
    ("PAYROLL ACME LTD SALARY", ["rent"], False),
    ("PRET A MANGER LONDON", ["client dinners", "food"], True),
    ("AMZN MKTPLACE PMU34KQ", ["office supplies"], False),
]


def eval_keyword_thresholds():
    """Raw noul probabilities per case → accuracy at each candidate threshold."""
    probs = []
    by_categories = {}
    for desc, categories, _ in KEYWORD_CASES:
        by_categories.setdefault(tuple(categories), []).append(desc)
    for categories, descriptions in by_categories.items():
        state = {
            "categories": list(categories),
            "lines": [{"n": i, "description": d} for i, d in enumerate(descriptions)],
        }
        questions = {}
        for i in range(len(descriptions)):
            questions["line_{}".format(i)] = judgment.Noul(
                instructions=(
                    "Transaction description `lines[{i}].description` from a bank or "
                    "credit card statement. The user is filtering their statement and "
                    "wants to KEEP transactions that belong to any of their categories "
                    "`categories`. Category names are informal, so judge the merchant's "
                    "core business rather than exact words: 'SHELL' belongs to 'fuel', "
                    "'TFL TRAVEL CHARGE' belongs to 'travel'. Overlapping words alone "
                    "do not count, and unclear cases must be false."
                ).format(i=i),
                criteria={
                    "true": "The transaction clearly belongs to at least one listed category.",
                    "false": "It belongs to none of the categories, or that is unclear.",
                },
            )
        answers = judgment.decide_batch(state, questions)
        if answers is None:
            return None
        for i, desc in enumerate(descriptions):
            expected = next(e for d, c, e in KEYWORD_CASES
                            if d == desc and list(c) == list(categories))
            probs.append((desc, categories, expected,
                          getattr(answers["line_{}".format(i)], "noul", None)))

    print("\n== Part A1: keyword matching ({} labeled cases) ==".format(len(KEYWORD_CASES)))
    print("{:>9}  {:>7}  {}".format("threshold", "acc", "errors"))
    best = (None, -1.0)
    for threshold in (0.5, 0.6, 0.7, 0.75, 0.8, 0.9):
        correct, errors = 0, []
        for desc, categories, expected, p in probs:
            if p is None:
                continue
            got = p >= threshold
            correct += got == expected
            if got != expected:
                errors.append("{}~{} p={:.2f}".format(desc, "/".join(categories), p))
        acc = correct / len(probs)
        if acc > best[1]:
            best = (threshold, acc)
        print("{:>9.2f}  {:>6.0%}  {}".format(threshold, acc, "; ".join(errors) or "-"))
    print("  best threshold: {} ({:.0%}) — module default: {}".format(
        best[0], best[1], judgment.KEYWORD_THRESHOLD))


def eval_name_disambiguation():
    """Merchant-vs-person choice over the labeled name set."""
    state = {"candidates": [{"n": i, "text": t} for i, (t, _) in enumerate(NAME_CASES)]}
    questions = {
        "cand_{}".format(i): judgment.Choice(
            instructions=(
                "Text `candidates[{i}].text` was flagged on a statement inside its "
                "transaction region. What kind of name is it?"
            ).format(i=i),
            criteria={
                "merchant": "A shop, company, or service provider (e.g. a store, utility, transport operator).",
                "person": "A person's name (possibly with a title like Mr/Ms/Dr).",
                "neither": "Neither — e.g. a reference number fragment, an address line, or unclassifiable.",
            },
        )
        for i in range(len(NAME_CASES))
    }
    answers = judgment.decide_batch(state, questions)
    if answers is None:
        return None

    print("\n== Part A2: merchant-vs-person ({} labeled cases) ==".format(len(NAME_CASES)))
    correct, low_conf = 0, 0
    for i, (text, is_person) in enumerate(NAME_CASES):
        a = answers["cand_{}".format(i)]
        judged_person = a.choice == "person" and (a.confidence or 0) >= judgment.PERSON_CONFIDENCE
        correct += judged_person == is_person
        if a.choice == "person" and (a.confidence or 0) < judgment.PERSON_CONFIDENCE:
            low_conf += 1
        flag = "OK " if judged_person == is_person else "MISS"
        print("  [{}] {:<32} person={:<5} choice={:<8} conf={:.2f}".format(
            flag, text[:32], str(is_person), str(a.choice), a.confidence or 0))
    print("  accuracy: {:.0%}  (person confidence default: {}, low-conf person answers: {})".format(
        correct / len(NAME_CASES), judgment.PERSON_CONFIDENCE, low_conf))


def eval_instruction_thresholds():
    """Free-text removal instructions over labeled transaction rows."""
    INSTRUCTION_CASES = [
        ("BET365 CASINO", "gambling and crypto", True),
        ("CRYPTO.COM PURCHASE", "gambling and crypto", True),
        ("SKY BET LIMITED", "gambling and crypto", True),
        ("TESCO STORES 3487", "gambling and crypto", False),
        ("NETFLIX.COM", "gambling and crypto", False),
        ("BET365 CASINO", "streaming subscriptions", False),
        ("NETFLIX.COM", "streaming subscriptions", True),
        ("SPOTIFY UK", "streaming subscriptions", True),
        ("DISNEY+ LONDON", "streaming subscriptions", True),
        ("SHELL GSTATIONS LONDON", "streaming subscriptions", False),
        ("VIRGIN MEDIA", "streaming subscriptions", False),
        ("PRET A MANGER LONDON", "coffee shops and takeaways", True),
        ("STARBUCKS CARD LONDON", "coffee shops and takeaways", True),
        ("PAYROLL ACME LTD SALARY", "coffee shops and takeaways", False),
    ]
    by_instruction = {}
    for desc, instruction, _ in INSTRUCTION_CASES:
        by_instruction.setdefault(instruction, []).append(desc)

    probs = []
    for instruction, descriptions in by_instruction.items():
        state = {
            "instruction": instruction,
            "lines": [{"n": i, "description": d} for i, d in enumerate(descriptions)],
        }
        questions = {}
        for i in range(len(descriptions)):
            questions["line_{}".format(i)] = judgment.Noul(
                instructions=(
                    "Transaction description `lines[{i}].description` from the user's "
                    "own bank or credit card statement. The user asked to remove "
                    "transactions matching their instruction `instruction`. Does this "
                    "transaction match what the user described? Judge the merchant's "
                    "core business, not exact words ('Bet365 Casino' matches 'gambling'; "
                    "'TESCO' does not). Unclear or borderline cases must be false — "
                    "this decides what gets permanently redacted."
                ).format(i=i),
                criteria={
                    "true": "The transaction clearly matches the user's removal instruction.",
                    "false": "It does not match, or that is unclear.",
                },
            )
        answers = judgment.decide_batch(state, questions)
        if answers is None:
            return None
        for i, desc in enumerate(descriptions):
            expected = next(e for d, ins, e in INSTRUCTION_CASES
                            if d == desc and ins == instruction)
            probs.append((desc, instruction, expected,
                          getattr(answers["line_{}".format(i)], "noul", None)))

    print("\n== Part A3: plain-English instructions ({} labeled cases) ==".format(
        len(INSTRUCTION_CASES)))
    print("{:>9}  {:>7}  {}".format("threshold", "acc", "errors"))
    best = (None, -1.0)
    for threshold in (0.5, 0.6, 0.7, 0.75, 0.8, 0.9):
        correct, errors = 0, []
        for desc, instruction, expected, p in probs:
            if p is None:
                continue
            got = p >= threshold
            correct += got == expected
            if got != expected:
                errors.append("{}~'{}' p={:.2f}".format(desc, instruction, p))
        acc = correct / len(probs)
        if acc > best[1]:
            best = (threshold, acc)
        print("{:>9.2f}  {:>6.0%}  {}".format(threshold, acc, "; ".join(errors) or "-"))
    print("  best threshold: {} ({:.0%}) — module default: {}".format(
        best[0], best[1], judgment.KEYWORD_THRESHOLD))


# ── Part B: real statement fixtures, baseline vs flags-on ───────────────────
EVAL_KEYWORDS = ["rent", "tfl", "uber", "salary"]


def eval_statement_fixtures():
    from redact_bank_generic import redact_bank_generic
    import tempfile

    fixture_dir = os.environ.get(
        "E2E_STATEMENTS_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "e2e_statements"))
    pdfs = sorted(glob.glob(os.path.join(fixture_dir, "*.pdf")))
    if not pdfs:
        print("\n== Part B skipped: no fixture PDFs in {} ==".format(fixture_dir))
        return

    print("\n== Part B: statement fixtures (keywords: {}) ==".format(", ".join(EVAL_KEYWORDS)))
    for pdf in pdfs:
        with tempfile.TemporaryDirectory() as tmp:
            base_out = os.path.join(tmp, "base.pdf")
            sem_out = os.path.join(tmp, "sem.pdf")
            for var in ("JEV_SEMANTIC_KEYWORDS", "JEV_PII_DISAMBIGUATION", "JEV_GENERIC_ROWS"):
                os.environ.pop(var, None)
            _, b_total, b_kept = redact_bank_generic(pdf, base_out, EVAL_KEYWORDS)

            os.environ["JEV_SEMANTIC_KEYWORDS"] = "on"
            os.environ["JEV_PII_DISAMBIGUATION"] = "on"
            os.environ["JEV_GENERIC_ROWS"] = "on"
            try:
                _, s_total, s_kept = redact_bank_generic(pdf, sem_out, EVAL_KEYWORDS)
            finally:
                for var in ("JEV_SEMANTIC_KEYWORDS", "JEV_PII_DISAMBIGUATION", "JEV_GENERIC_ROWS"):
                    os.environ.pop(var, None)

        b_descs = {r["description"] for r in b_kept}
        s_descs = {r["description"] for r in s_kept}
        print("\n  {} — baseline: {} rows £{:.2f} | jev: {} rows £{:.2f}".format(
            os.path.basename(pdf), len(b_kept), b_total, len(s_kept), s_total))
        for d in sorted(s_descs - b_descs):
            print("    + kept by Jev:    {}".format(d))
        for d in sorted(b_descs - s_descs):
            print("    - dropped by Jev: {}".format(d))
        if s_descs == b_descs:
            print("    (no flips)")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-statements", action="store_true",
                        help="skip Part B (real fixture PDFs)")
    args = parser.parse_args()

    if not judgment._HAS_TYPESAFE:
        print("typesafe-sdk is not installed: venv/bin/pip install typesafe-sdk")
        sys.exit(1)
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        print("TYPESAFE_API_KEY is not set — export it first (nothing is sent without it).")
        sys.exit(1)

    for var in judgment._FLAGS.values():
        os.environ[var] = "on"

    eval_keyword_thresholds()
    eval_name_disambiguation()
    eval_instruction_thresholds()
    if not args.skip_statements:
        eval_statement_fixtures()
    print("\nDone. Tune judgment.py thresholds on these numbers before enabling flags in prod.")


if __name__ == "__main__":
    main()
