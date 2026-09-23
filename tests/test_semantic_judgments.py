"""Seam tests for the opt-in Jev features wired into the redaction engines.

All judgments are monkeypatched — no test touches the network. Each test
pins the same contract from both sides: with the flags off (the default)
behaviour is byte-identical to the pre-Jev engine, and with a flag on the
Jev verdict extends (never replaces) the regex/substring behaviour, failing
open when the judgment layer has no verdict.
"""
import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import judgment
from redact_bank_generic import redact_bank_generic, _apply_pii_pass
from redact_generic import _attach_semantic_verdicts
from test_bank_generic import build_statement


# ── Idea 2: semantic keyword matching (bank engine) ─────────────────────────
def test_flag_off_never_calls_jev(tmp_path, monkeypatch):
    monkeypatch.delenv("JEV_SEMANTIC_KEYWORDS", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    def boom(*a, **k):
        raise AssertionError("Jev must not be invoked with the feature flag off")

    monkeypatch.setattr(judgment, "semantic_keep_batch", boom)
    build_statement(tmp_path / "in.pdf")
    _, total, kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf"), ["rent"])
    assert len(kept) == 1 and total == pytest.approx(1200.0)


def test_semantic_verdict_keeps_row_substring_misses(tmp_path, monkeypatch):
    build_statement(tmp_path / "in.pdf")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("JEV_SEMANTIC_KEYWORDS", "on")
    seen = {}

    def fake_batch(keywords, descriptions, threshold=judgment.KEYWORD_THRESHOLD):
        seen["keywords"] = list(keywords)
        seen["descriptions"] = list(descriptions)
        return [d == "Netflix" for d in descriptions]

    monkeypatch.setattr(judgment, "semantic_keep_batch", fake_batch)

    # "entertainment" substring-matches nothing on the statement; only the
    # Jev verdict keeps Netflix.
    _, total, kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf"), ["entertainment"])

    assert seen["keywords"] == ["entertainment"]
    assert "Netflix" in seen["descriptions"]
    assert [r["description"] for r in kept] == ["Netflix"]
    assert total == pytest.approx(12.99)

    text = fitz.open(str(tmp_path / "out.pdf"))[0].get_text()
    assert "Netflix" in text
    assert "Tesco" not in text                    # judged False → redacted
    assert "954.80" not in text and "1233.06" in text  # balances never touched


def test_semantic_failure_falls_back_to_substring(tmp_path, monkeypatch):
    build_statement(tmp_path / "in.pdf")
    baseline_out = tmp_path / "baseline.pdf"
    jev_out = tmp_path / "jev.pdf"

    # Baseline: flag off, keyword matches nothing → everything redacted.
    _, base_total, base_kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(baseline_out), ["entertainment"])

    # Jev on but the batch fails → must reproduce the baseline exactly.
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("JEV_SEMANTIC_KEYWORDS", "on")
    monkeypatch.setattr(judgment, "semantic_keep_batch",
                        lambda *a, **k: None)
    _, jev_total, jev_kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(jev_out), ["entertainment"])

    assert base_kept == jev_kept == []
    assert base_total == jev_total == 0.0
    assert fitz.open(str(baseline_out))[0].get_text() == \
        fitz.open(str(jev_out))[0].get_text()


# ── Idea 2: semantic keyword matching (card engine helper) ──────────────────
def test_attach_semantic_verdicts_stamps_visual_lines(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("JEV_SEMANTIC_KEYWORDS", "on")
    spans = [
        {"text": "SHELL", "y": 100.0},
        {"text": "10.00", "y": 100.4},     # same visual line (within 2pt)
        {"text": "NETFLIX", "y": 120.0},
    ]
    monkeypatch.setattr(
        judgment, "semantic_keep_batch",
        lambda keywords, descriptions, threshold=0.75:
            [d.startswith("SHELL") for d in descriptions])

    _attach_semantic_verdicts(spans, ["fuel"])

    assert spans[0]["semantic"] is True
    assert spans[1]["semantic"] is True
    assert spans[2]["semantic"] is False


def test_attach_semantic_verdicts_disabled_leaves_unstamped(monkeypatch):
    monkeypatch.delenv("JEV_SEMANTIC_KEYWORDS", raising=False)
    spans = [{"text": "SHELL", "y": 100.0}]
    _attach_semantic_verdicts(spans, ["fuel"])
    assert "semantic" not in spans[0]


# ── Idea 1: PII merchant-vs-person disambiguation ───────────────────────────
def _pii_findings():
    header_y = 100.0
    return [
        # Above the header: always kept (account-holder name).
        {"entity_type": "PERSON", "score": 0.9, "bbox": (50, 60, 150, 70), "page_number": 0},
        # Below the header, PERSON candidates (the old blanket-drop zone).
        {"entity_type": "PERSON", "score": 0.8, "bbox": (110, 150, 220, 160), "page_number": 0},
        {"entity_type": "PERSON", "score": 0.8, "bbox": (110, 170, 220, 180), "page_number": 0},
        # Below the header: PHONE stays dropped even in semantic mode.
        {"entity_type": "PHONE_NUMBER", "score": 0.9, "bbox": (300, 180, 400, 190), "page_number": 0},
        # Below the header: structured types always survive.
        {"entity_type": "IBAN_CODE", "score": 0.9, "bbox": (50, 190, 200, 200), "page_number": 0},
    ], header_y


def _patch_pii_layer(monkeypatch, findings):
    import pii_layer

    captured = {}

    def fake_analyze(page, header_y=None, structured_only=False,
                     exclude_types=frozenset()):
        captured["header_y"] = header_y
        return list(findings)

    monkeypatch.setattr(pii_layer, "analyze_page_pii", fake_analyze)

    redacted = []
    monkeypatch.setattr(pii_layer, "apply_pii_redactions",
                        lambda page, findings: redacted.extend(findings))
    return captured, redacted


def test_apply_pii_pass_semantic_disambiguation(monkeypatch):
    findings, header_y = _pii_findings()
    captured, redacted = _patch_pii_layer(monkeypatch, findings)
    monkeypatch.setattr(judgment, "enabled", lambda feature: True)
    monkeypatch.setattr(judgment, "classify_person_like",
                        lambda page, candidates: [0])  # first candidate is a person

    _apply_pii_pass(page=None, page_num=0, header_y=header_y, pii_mode="enforce")

    assert captured["header_y"] is None            # full-page analysis, rule re-applied
    bboxes = [f["bbox"] for f in redacted]
    assert (50, 60, 150, 70) in bboxes             # above-header PERSON
    assert (110, 150, 220, 160) in bboxes          # below-header PERSON judged person
    assert (110, 170, 220, 180) not in bboxes      # judged merchant → dropped
    assert (300, 180, 400, 190) not in bboxes      # PHONE still dropped
    assert (50, 190, 200, 200) in bboxes           # structured IBAN survives


def test_apply_pii_pass_default_delegates_to_pii_layer(monkeypatch):
    findings, header_y = _pii_findings()
    captured, redacted = _patch_pii_layer(monkeypatch, findings)
    monkeypatch.delenv("JEV_PII_DISAMBIGUATION", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    def boom(*a, **k):
        raise AssertionError("Jev must not be invoked with the feature flag off")

    monkeypatch.setattr(judgment, "classify_person_like", boom)
    _apply_pii_pass(page=None, page_num=0, header_y=header_y, pii_mode="enforce")

    # The header_y blanket drop is pii_layer's own (tested) behaviour: the
    # caller must pass header_y straight through unchanged, single call.
    assert captured["header_y"] == header_y
    assert len(redacted) == len(findings)          # stub returns everything


# ── Idea 3: Jev header fallback for unknown layouts ─────────────────────────
def _build_unknown_layout(path):
    """A statement whose header labels defeat ROLE_LABELS: When / Who to /
    How much / Left. Regex detection finds no credible header line."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 60), "Sort Code 12-34-56")
    for text, x in [("When", 50), ("Who to", 110), ("How much", 340), ("Left", 510)]:
        page.insert_text((x, 110), text)
    for y, cells in [
        (140, [("01 May", 50), ("Tesco", 110), ("45.20", 340), ("954.80", 510)]),
        (162, [("02 May", 50), ("Netflix", 110), ("12.99", 340), ("941.81", 510)]),
    ]:
        for text, x in cells:
            page.insert_text((x, y), text)
    doc.save(str(path))
    doc.close()


def test_semantic_header_fallback_redacts_unknown_layout(tmp_path, monkeypatch):
    _build_unknown_layout(tmp_path / "in.pdf")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("JEV_GENERIC_ROWS", "on")

    def fake_pick(candidates):
        assert any("When" in text for _, text in candidates)
        return 1                                    # the header row

    # assign_header_roles receives phrase texts in left-to-right order.
    def fake_assign(phrase_texts):
        table = {"When": "date", "Who to": "description",
                 "How much": "paid_out", "Left": "balance"}
        return [table.get(t, "none") for t in phrase_texts]

    monkeypatch.setattr(judgment, "pick_header_line", fake_pick)
    monkeypatch.setattr(judgment, "assign_header_roles", fake_assign)

    _, total, kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf"), ["netflix"])

    assert [r["description"] for r in kept] == ["Netflix"]
    assert total == pytest.approx(12.99)
    text = fitz.open(str(tmp_path / "out.pdf"))[0].get_text()
    assert "Netflix" in text
    assert "Tesco" not in text                      # redacted via the Jev bands
    assert "954.80" in text                         # balance column preserved
    assert "When" in text                           # header row itself untouched


def test_semantic_header_fallback_off_leaves_page_untouched(tmp_path, monkeypatch):
    _build_unknown_layout(tmp_path / "in.pdf")
    monkeypatch.delenv("JEV_GENERIC_ROWS", raising=False)

    def boom(*a, **k):
        raise AssertionError("Jev must not be invoked with the feature flag off")

    monkeypatch.setattr(judgment, "pick_header_line", boom)
    _, total, kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf"), ["netflix"])

    assert kept == []
    text = fitz.open(str(tmp_path / "out.pdf"))[0].get_text()
    assert "Tesco" in text                          # page untouched, nothing lost
    assert "Netflix" in text


def test_semantic_header_fallback_low_credibility_fails_open(tmp_path, monkeypatch):
    _build_unknown_layout(tmp_path / "in.pdf")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("JEV_GENERIC_ROWS", "on")
    monkeypatch.setattr(judgment, "pick_header_line", lambda candidates: 1)
    # Jev maps too few roles → the credibility bar rejects the line.
    monkeypatch.setattr(judgment, "assign_header_roles",
                        lambda phrase_texts: ["description"] * len(phrase_texts))

    _, total, kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf"), ["netflix"])

    assert kept == []
    text = fitz.open(str(tmp_path / "out.pdf"))[0].get_text()
    assert "Tesco" in text                          # untouched, like the regex path
