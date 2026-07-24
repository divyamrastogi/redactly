"""
Presidio-backed PII layer for pdf-redact (report-only first pass).

This module wraps Microsoft Presidio (``presidio-analyzer`` + spaCy
``en_core_web_sm``) to find personally-identifiable information in the
*non-transaction* parts of a bank/card statement PDF — the page furniture
(account-holder name, sort code, account number, IBAN, NI number) that lives
above the transaction table.

Hard lesson from benchmarking: inside the transaction section Presidio
reliably mis-flags merchant names as PERSON/ORG. So findings that fall below
the detected transaction ``header_y`` are dropped — *unless* their entity type
is in :data:`STRUCTURED_TYPES` (IBAN / card / sort-code / account-number /
NINO / email), which are format-validated and never merchant names.

PRIVACY RULE (hard): no function, log line, or CLI output in this module ever
exposes the matched text of a finding. Only entity type, score, bbox and page
number are ever produced. Tests assert this.

The module imports cleanly even when ``presidio-analyzer`` is not installed;
analysis raises :class:`RuntimeError` only when actually invoked.
"""

from collections import Counter

import fitz  # core dependency of the app; always present

# Layout helpers reused from the existing redactor so the PII layer sees the
# exact same span model and column geometry as the transaction redaction.
from redact_bank_generic import _collect_spans, _detect_columns, _pad_rect

# --- Guarded Presidio import -------------------------------------------------
# The module must import without presidio; the heavy objects are only touched
# when analysis is actually requested.
try:
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    _HAS_PRESIDIO = True
except ImportError:  # pragma: no cover - exercised only on minimal installs
    _HAS_PRESIDIO = False
    AnalyzerEngine = Pattern = PatternRecognizer = NlpEngineProvider = None


# --- Entity taxonomy ---------------------------------------------------------
# Only these are ever acted on. Noisy types (ORG, DATE_TIME, LOCATION, NRP,
# URL) are deliberately excluded — benchmarked to produce merchant/narrative
# false positives.
ENTITIES = (
    "PERSON",
    "IBAN_CODE",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "UK_SORT_CODE",
    "UK_ACCOUNT_NUMBER",
    "UK_NINO",
)

# Format-validated entity types that are never merchant names, so they are safe
# to report (and redact) INSIDE the transaction region too. PERSON and
# PHONE_NUMBER are NOT here — a merchant name or a merchant phone can appear in
# a transaction line, so those are only reported above ``header_y``.
STRUCTURED_TYPES = frozenset({
    "IBAN_CODE",
    "CREDIT_CARD",
    "UK_SORT_CODE",
    "UK_ACCOUNT_NUMBER",
    "UK_NINO",
    "EMAIL_ADDRESS",
})

# Landlord mode keeps proof of account ownership: the statement must show WHOSE
# account received the rent, so the holder's name and the account identifiers
# stay visible. Everything else (card numbers, NINO, email, phone) is still
# redacted.
LANDLORD_PRESERVED_TYPES = frozenset({
    "PERSON",
    "UK_ACCOUNT_NUMBER",
    "UK_SORT_CODE",
    "IBAN_CODE",
})


def _uk_recognizers():
    """Custom UK PatternRecognizers, registered on the lazy analyzer.

    Each carries context words so a bare pattern hit only clears the score
    threshold when surrounded by the right vocabulary (e.g. an 8-digit number
    alone is ambiguous; "account … 12345678" is not).
    """
    return [
        PatternRecognizer(
            supported_entity="UK_SORT_CODE",
            name="UKSortCodeRecognizer",
            patterns=[
                Pattern("uk_sort_code", r"\b\d{2}-\d{2}-\d{2}\b", 0.5),
                # Some banks (Revolut) print the sort code as 6 contiguous
                # digits. Far too ambiguous alone (dates, references), so the
                # base score keeps it below threshold — it only surfaces when
                # the "sort code" context words boost it.
                Pattern("uk_sort_code_plain", r"\b\d{6}\b", 0.1),
            ],
            context=["sort code", "sort-code"],
        ),
        PatternRecognizer(
            supported_entity="UK_ACCOUNT_NUMBER",
            name="UKAccountNumberRecognizer",
            patterns=[Pattern("uk_account_number", r"\b\d{8}\b", 0.3)],
            context=["account", "account number", "a/c"],
        ),
        PatternRecognizer(
            supported_entity="UK_NINO",
            name="UKNinoRecognizer",
            patterns=[Pattern(
                "uk_nino", r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b", 0.5)],
            context=["national insurance", "ni number"],
        ),
    ]


# --- Lazy analyzer singleton -------------------------------------------------
_analyzer = None


def _get_analyzer():
    """Return the shared AnalyzerEngine, building it on first use.

    Raises RuntimeError if presidio is not installed.
    """
    global _analyzer
    if _analyzer is None:
        if not _HAS_PRESIDIO:
            raise RuntimeError(
                "presidio-analyzer not installed; "
                "pip install presidio-analyzer spacy && "
                "python -m spacy download en_core_web_sm"
            )
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        })
        engine = AnalyzerEngine(
            nlp_engine=provider.create_engine(),
            supported_languages=["en"],
        )
        for recognizer in _uk_recognizers():
            engine.registry.add_recognizer(recognizer)
        _analyzer = engine
    return _analyzer


# --- Offset -> span geometry -------------------------------------------------
def _build_offset_spans(spans):
    """Join span texts with single spaces, returning (joined_text, offset_spans).

    ``offset_spans`` is a list of ``(start, end, bbox)`` tuples where
    ``[start, end)`` is the half-open char range the span occupies inside
    ``joined_text``.
    """
    offset_spans = []
    parts = []
    pos = 0
    for s in spans:
        text = s["text"]
        start = pos
        end = pos + len(text)
        offset_spans.append((start, end, tuple(s["bbox"])))
        parts.append(text)
        pos = end + 1  # one space separator between spans
    return " ".join(parts), offset_spans


def _span_bbox_for_offset(offset, offset_spans):
    """Bbox of the span whose char range covers ``offset``.

    Entities never begin on the inter-span space, so ``offset`` normally lands
    inside a span's [start, end). A defensive nearest-span fallback keeps us
    from dropping a finding over an off-by-one.
    """
    for start, end, bbox in offset_spans:
        if start <= offset < end:
            return bbox
    best, best_dist = None, None
    for start, end, bbox in offset_spans:
        dist = 0 if start <= offset <= end else min(abs(offset - start), abs(offset - end))
        if best_dist is None or dist < best_dist:
            best, best_dist = bbox, dist
    return best


# --- Public API --------------------------------------------------------------
def analyze_page_pii(page, header_y=None, min_score=0.4,
                     structured_only=False, exclude_types=frozenset()):
    """Find PII on a single PDF page, honouring the transaction-region rule.

    Spans are collected exactly as the redactor sees them (:func:`_collect_spans`),
    joined into one string with a char-offset -> span-bbox map, and run through
    Presidio once. Each result's start offset is mapped back to its covering
    span's bbox.

    A finding whose mapped span lies below ``header_y`` is dropped unless its
    entity type is in :data:`STRUCTURED_TYPES`. When ``header_y`` is None (no
    transaction header detected on the page) the whole page is analysed and no
    findings are dropped by position.

    ``structured_only=True`` restricts findings to :data:`STRUCTURED_TYPES`.
    Callers set it for headerless pages that FOLLOW the transaction pages —
    those are info/terms pages where PERSON/PHONE hits are the bank's own
    contact details (benchmarked on a real statement: 8 helpline numbers on one
    page), not the customer's. A headerless page seen before any transaction
    header may be a cover page carrying the customer's address block, so the
    caller only applies this once a header has been seen.

    ``exclude_types`` are never reported at all — e.g. pass
    :data:`LANDLORD_PRESERVED_TYPES` in landlord mode, where the statement must
    keep proving whose account received the rent. Preservation also SHIELDS the
    excluded characters: any other finding whose char range overlaps an
    excluded-type detection is dropped too. Without this, a colliding
    recognizer re-redacts the preserved text under another label — seen on a
    real statement where the phone recognizer matched the entire
    "sort code + account number" digit run.

    Returns a list of ``{"entity_type", "score", "bbox", "page_number"}`` dicts.
    Never includes matched text.
    """
    analyzer = _get_analyzer()
    spans = _collect_spans(page)
    if not spans:
        return []

    joined, offset_spans = _build_offset_spans(spans)
    results = analyzer.analyze(
        text=joined, entities=list(ENTITIES), language="en",
    )

    # Char ranges of excluded (preserved) detections — see the shield note in
    # the docstring. Only confident detections shield, mirroring min_score.
    excluded_hits = [
        (r.start, r.end) for r in results
        if r.entity_type in exclude_types and r.score >= min_score
    ]

    findings = []
    for r in results:
        if r.score < min_score:
            continue
        if r.entity_type in exclude_types:
            continue
        if any(r.start < e and s < r.end for s, e in excluded_hits):
            continue
        if structured_only and r.entity_type not in STRUCTURED_TYPES:
            continue
        bbox = _span_bbox_for_offset(r.start, offset_spans)
        if bbox is None:
            continue
        # Transaction-region guard: drop merchant-shaped findings below the
        # header unless the type is format-validated (STRUCTURED_TYPES).
        if (header_y is not None
                and bbox[1] > header_y
                and r.entity_type not in STRUCTURED_TYPES):
            continue
        findings.append({
            "entity_type": r.entity_type,
            "score": float(r.score),
            "bbox": tuple(bbox),
            "page_number": page.number,
        })
    return findings


def apply_pii_redactions(page, findings):
    """True-redact every finding bbox on ``page`` and apply in one pass.

    Reuses :func:`redact_bank_generic._pad_rect` so the redaction rects match
    the rest of the app (horizontally padded, vertically inset to avoid
    clipping adjacent kept rows).
    """
    for finding in findings:
        page.add_redact_annot(_pad_rect(finding["bbox"]), fill=(0, 0, 0))
    page.apply_redactions()


# --- CLI ---------------------------------------------------------------------
def _page_counts(findings):
    """``{entity_type: count}`` for a page's findings — text-free summary."""
    return dict(Counter(f["entity_type"] for f in findings))


def main(argv=None):
    """``python pii_layer.py <pdf> [--enforce out.pdf]``.

    Report mode (default): print per-page finding counts by entity type.
    Enforce mode: additionally redact findings and write the result PDF.
    Output never contains matched text.
    """
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("pdf", help="input statement PDF")
    parser.add_argument(
        "--enforce", metavar="OUT", default=None,
        help="redact findings and write the result to OUT (default: report only)",
    )
    parser.add_argument(
        "--landlord", action="store_true",
        help="preserve account-ownership PII (holder name, account number, "
             "sort code, IBAN) as landlord statements must prove whose "
             "account received the rent",
    )
    args = parser.parse_args(argv)

    exclude = LANDLORD_PRESERVED_TYPES if args.landlord else frozenset()
    doc = fitz.open(args.pdf)
    n_pages = len(doc)
    total = Counter()
    seen_header = False
    for page in doc:
        _, header_y = _detect_columns(_collect_spans(page), page.rect.width)
        findings = analyze_page_pii(
            page, header_y=header_y,
            structured_only=(header_y is None and seen_header),
            exclude_types=exclude,
        )
        seen_header = seen_header or header_y is not None
        counts = _page_counts(findings)
        total.update(counts)
        print(f"page {page.number + 1}: {counts or 'no findings'}")
        if args.enforce:
            apply_pii_redactions(page, findings)

    if args.enforce:
        doc.save(args.enforce)
        print(f"wrote redacted output: {args.enforce}")
    doc.close()

    print(f"total across {n_pages} page(s): {dict(total) or 'no findings'}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
