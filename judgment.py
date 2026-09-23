"""
TypeSafe Jev judgment layer for pdf-redact (opt-in, env-gated).

Wraps the TypeSafe System One API so the redaction engines can ask small
semantic questions ("does this merchant belong to the user's 'fuel'
category?", "is this a merchant name or a person's name?") and get typed,
calibrated answers back instead of prompt-and-parse JSON.

PRIVACY (hard): every Jev feature is OFF unless its env flag is set to
``on`` AND ``TYPESAFE_API_KEY`` is present. With the default configuration
this module is inert and no statement content ever leaves the process.
When a flag IS on, the text needed to answer the question (transaction
descriptions, ambiguous name candidates) is sent to the TypeSafe API for
classification. Accordingly, no function, log line, or CLI output in this
module ever logs that text — only counts, timings, and outcome. Tests
assert this.

FAIL-OPEN (hard): redaction must never break because the judgment API is
down, slow, or returns something unexpected. Every entry point returns
``None`` (or an empty result) on any failure and the callers fall back to
the pre-existing regex/Presidio behaviour. Nothing in here raises.

BATCHING: one HTTP request carries one shared ``state`` plus a map of many
questions, answered in parallel server-side. Callers collect their
candidates and issue one call per page — never one call per line. The
per-request question limit is undocumented, so :func:`decide_batch`
defensively chunks at :data:`MAX_QUESTIONS_PER_REQUEST`.
"""

import logging
import math
import os
import time

logger = logging.getLogger(__name__)

# Guarded import: the module must import cleanly without the SDK installed
# (same contract as pii_layer's guarded presidio import).
try:
    from typesafe_sdk import (
        Choice,
        Noul,
        RetryPolicy,
        TypeSafeClient,
        TypeSafeError,
    )

    _HAS_TYPESAFE = True
except ImportError:  # pragma: no cover - exercised only on minimal installs
    _HAS_TYPESAFE = False
    Choice = Noul = RetryPolicy = TypeSafeClient = TypeSafeError = None

_MODEL = "jev-latest"
DEFAULT_TIMEOUT = 20.0
MAX_QUESTIONS_PER_REQUEST = 40

# Thresholds chosen as starting points; eval_judgments.py sweeps them on
# real statement data before the flags are enabled in production.
KEYWORD_THRESHOLD = 0.75
PERSON_CONFIDENCE = 0.6
HEADER_CONFIDENCE = 0.5

# Per-question caps keep a single page's batch small and bounded.
MAX_PII_CANDIDATES_PER_PAGE = 20
MAX_HEADER_CANDIDATES = 60

# feature name -> env var. Read at call time (the repo's UMAMI idiom), so
# platform config vars (``railway variables``) take effect without code changes.
_FLAGS = {
    "semantic_keywords": "JEV_SEMANTIC_KEYWORDS",
    "pii_disambiguation": "JEV_PII_DISAMBIGUATION",
    "generic_rows": "JEV_GENERIC_ROWS",
}


def _flag_on(var):
    return os.environ.get(var, "").strip().lower() == "on"


def _api_key():
    return os.environ.get("TYPESAFE_API_KEY", "").strip()


def enabled(feature):
    """True when *feature*'s flag is ``on`` and the SDK + API key are usable."""
    if not _HAS_TYPESAFE:
        return False
    var = _FLAGS.get(feature)
    if var is None or not _flag_on(var):
        return False
    return bool(_api_key())


def decide_batch(state, questions, timeout=DEFAULT_TIMEOUT):
    """Answer a map of ``question_id -> question`` against one shared state.

    Questions are chunked into batches of :data:`MAX_QUESTIONS_PER_REQUEST`
    (the API's per-request question limit is undocumented). Returns the
    merged ``{question_id: answer}`` map, or ``None`` if any chunk failed or
    came back incomplete — callers must treat ``None`` as "no verdicts, use
    the fallback path". All-or-nothing on purpose: partial verdicts across a
    page would make behaviour hard to reason about and test.
    """
    if not questions:
        return {}
    if not _HAS_TYPESAFE or not _api_key():
        return None

    items = list(questions.items())
    answers = {}
    for start in range(0, len(items), MAX_QUESTIONS_PER_REQUEST):
        chunk = dict(items[start:start + MAX_QUESTIONS_PER_REQUEST])
        chunk_answers = _decide_chunk(state, chunk, timeout)
        if chunk_answers is None:
            return None
        answers.update(chunk_answers)
    return answers


def _decide_chunk(state, chunk, timeout):
    """One TypeSafe request for one chunk. Returns answers or None."""
    started = time.monotonic()
    try:
        with TypeSafeClient(
            api_key=_api_key(),
            model=_MODEL,
            retry=RetryPolicy(max_retries=3, timeout=timeout),
            timeout=timeout,
        ) as client:
            response = client.system_one(state=state, questions=chunk)
    except TypeSafeError as exc:
        logger.warning(
            "jev batch failed: %s after %.2fs (%d questions)",
            type(exc).__name__, time.monotonic() - started, len(chunk),
        )
        return None
    except Exception as exc:  # fail-open contract: never propagate
        logger.warning(
            "jev batch failed: %s after %.2fs (%d questions)",
            type(exc).__name__, time.monotonic() - started, len(chunk),
        )
        return None

    answers = {qid: response.answers.get(qid) for qid in chunk}
    missing = sum(1 for a in answers.values() if a is None)
    if missing:
        logger.warning(
            "jev batch incomplete: %d/%d questions unanswered", missing, len(chunk),
        )
        return None
    logger.info(
        "jev batch ok: %d questions in %.2fs (%d input tokens)",
        len(chunk), time.monotonic() - started,
        getattr(response.usage, "input_tokens", 0) or 0,
    )
    return answers


def semantic_keep_batch(keywords, descriptions, threshold=KEYWORD_THRESHOLD):
    """Judge whether each transaction description belongs to the categories.

    ``keywords`` are the user's informal categories ("fuel", "travel",
    "client dinners"); ``descriptions`` are the transaction description
    strings from one statement. Returns a list aligned with ``descriptions``
    where each entry is True (keep), False (drop), or None (no verdict —
    caller falls back to substring matching for that line). Returns None
    when the whole batch failed (fail-open).
    """
    if not enabled("semantic_keywords") or not keywords or not descriptions:
        return None

    state = {
        "categories": [str(k) for k in keywords],
        "lines": [
            {"n": i, "description": d} for i, d in enumerate(descriptions)
        ],
    }
    questions = {
        "line_{}".format(i): Noul(
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
        for i in range(len(descriptions))
    }
    answers = decide_batch(state, questions)
    if answers is None:
        return None

    verdicts = []
    for i in range(len(descriptions)):
        prob = getattr(answers["line_{}".format(i)], "noul", None)
        verdicts.append(None if prob is None else prob >= threshold)
    return verdicts


def classify_person_like(page, findings):
    """Decide which PII findings below the transaction header are a person's name.

    ``findings`` are pii_layer finding dicts ({entity_type, score, bbox,
    page_number}) that the blanket ``header_y`` drop would have discarded.
    The text at each bbox is read here (pii_layer itself stays text-free)
    and one Choice question per candidate is asked in a single batched
    request. Returns the list of finding indices judged to be a person's
    name; [] when nothing qualified or the feature is off/failed (fail-open
    = the caller keeps its current drop behaviour).
    """
    if not enabled("pii_disambiguation") or not findings:
        return []

    import fitz

    candidates = findings[:MAX_PII_CANDIDATES_PER_PAGE]
    texts = []
    for f in candidates:
        rect = fitz.Rect(f["bbox"]) + (-1, -1, 1, 1)
        texts.append(" ".join((page.get_textbox(rect) or "").split()))
    # Skip candidates whose text could not be read — nothing to judge.
    judged = [(i, t) for i, t in enumerate(texts) if t]

    state = {
        "source": "bank or credit card statement, inside the transaction table region",
        "candidates": [{"n": i, "text": t} for i, t in judged],
    }
    questions = {
        "cand_{}".format(i): Choice(
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
        for i, _ in judged
    }
    answers = decide_batch(state, questions)
    if answers is None:
        return []

    person_indices = []
    for i, _ in judged:
        answer = answers["cand_{}".format(i)]
        if answer.choice == "person" and (answer.confidence or 0) >= PERSON_CONFIDENCE:
            person_indices.append(i)
    return person_indices


_HEADER_ROLES = {
    "date": "the column of transaction dates",
    "description": "the column of payee or transaction descriptions",
    "paid_out": "the column of money leaving the account (paid out, debits)",
    "paid_in": "the column of money entering the account (paid in, credits)",
    "balance": "the column of the running account balance",
    "amount": "the column of transaction amounts",
}


def assign_header_roles(phrase_texts):
    """Map each column-label phrase of a header line to a canonical role.

    ``phrase_texts`` are the merged phrases of the header line, in left-to-
    right order. Returns a list aligned with them where each entry is a role
    from :data:`_HEADER_ROLES` (``'none'``/None when not a column label), or
    None when the feature is off or the batch failed (fail-open: the page
    stays untouched). Banks outside the label table say things like
    'When / Who to / How much' — this is what makes those layouts workable.
    """
    if not enabled("generic_rows") or not phrase_texts:
        return None

    state = {"line": [{"n": i, "text": t} for i, t in enumerate(phrase_texts)]}
    questions = {
        "phrase_{}".format(i): Choice(
            instructions=(
                "Text `line[{i}].text` is one column label from the header row "
                "of a bank statement transaction table. Which column does it "
                "label? Informal wording is common ('When' = the date column, "
                "'To/From' or 'Who' = the description column)."
            ).format(i=i),
            criteria={
                **_HEADER_ROLES,
                "none": "Not a column label, or unclear.",
            },
        )
        for i in range(len(phrase_texts))
    }
    answers = decide_batch(state, questions)
    if answers is None:
        return None
    return [
        getattr(answers["phrase_{}".format(i)], "choice", None)
        for i in range(len(phrase_texts))
    ]


def pick_header_line(candidate_lines):
    """Pick the transaction-table column-header line from a statement page.

    ``candidate_lines`` is a list of ``(index, text)`` rows. One Choice
    question whose options are the candidate lines is asked in a single
    request. Returns the winning index, or None when the feature is
    off/failed or the answer is not confident — fail-open keeps the page
    untouched.
    """
    if not enabled("generic_rows") or not candidate_lines:
        return None
    if len(candidate_lines) > MAX_HEADER_CANDIDATES:
        candidate_lines = candidate_lines[:MAX_HEADER_CANDIDATES]

    state = {
        "source": "one page of a bank statement whose table column header must be located",
        "lines": [{"n": i, "text": t} for i, t in candidate_lines],
    }
    question = Choice(
        instructions=(
            "Exactly one of `lines` is the table header row of a bank statement "
            "transaction table: a short line of column labels such as 'Date', "
            "'Description', 'Type', 'Paid out', 'Paid in', 'Amount', 'Balance'. "
            "It sits directly above the transaction rows and contains no amounts "
            "or dates. Pick its line number; 'no_header' if none qualifies."
        ),
        criteria={
            **{"line_{}".format(i): "The line reading: {}".format(t)
               for i, t in candidate_lines},
            "no_header": "No line on this page is a transaction-table column header.",
        },
    )
    answers = decide_batch(state, {"header": question})
    if answers is None:
        return None

    answer = answers["header"]
    if not str(answer.choice).startswith("line_"):
        return None
    if (answer.confidence or 0) < HEADER_CONFIDENCE:
        return None
    try:
        return int(str(answer.choice).split("_", 1)[1])
    except (ValueError, IndexError):
        return None
