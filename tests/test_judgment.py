"""Tests for the TypeSafe Jev judgment layer (judgment.py).

Everything is mocked — no test ever touches the network. The suite pins the
module's three hard contracts:

1. gating: a feature is active only when its env flag is literally ``on`` AND
   ``TYPESAFE_API_KEY`` is present AND the SDK imported;
2. fail-open: any SDK/failure path returns ``None`` (or empty), never raises;
3. privacy: no statement text (state or question content) is ever logged.

Chunking behaviour (the undocumented per-request question limit) is covered
too.
"""
import logging
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import judgment


def _set_feature_on(monkeypatch, feature_env="JEV_SEMANTIC_KEYWORDS"):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv(feature_env, "on")


# ── Gating ──────────────────────────────────────────────────────────────────
def test_enabled_requires_flag_key_and_sdk(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("JEV_SEMANTIC_KEYWORDS", raising=False)
    monkeypatch.delenv("JEV_PII_DISAMBIGUATION", raising=False)
    assert not judgment.enabled("semantic_keywords")            # nothing set
    assert not judgment.enabled("unknown_feature")              # unregistered

    monkeypatch.setenv("JEV_SEMANTIC_KEYWORDS", "on")
    assert not judgment.enabled("semantic_keywords")            # flag but no key

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    assert judgment.enabled("semantic_keywords")

    monkeypatch.setenv("JEV_SEMANTIC_KEYWORDS", "OFF")          # only 'on' counts
    assert not judgment.enabled("semantic_keywords")

    monkeypatch.setenv("JEV_SEMANTIC_KEYWORDS", "on")
    monkeypatch.setenv("JEV_PII_DISAMBIGUATION", "on")
    assert judgment.enabled("pii_disambiguation")             # independent flags

    monkeypatch.setattr(judgment, "_HAS_TYPESAFE", False)
    assert not judgment.enabled("semantic_keywords")            # SDK missing


# ── decide_batch: chunking + fail-open ──────────────────────────────────────
def test_decide_batch_empty_questions(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert judgment.decide_batch({}, {}) == {}


def test_decide_batch_without_key_returns_none(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert judgment.decide_batch({"s": 1}, {"q": SimpleNamespace()}) is None


def test_decide_batch_chunks_questions(monkeypatch):
    _set_feature_on(monkeypatch)  # any flag; decide_batch only needs the key
    sizes, states = [], []

    def fake_chunk(state, chunk, timeout):
        states.append(state)
        sizes.append(len(chunk))
        return {qid: SimpleNamespace(noul=0.9) for qid in chunk}

    monkeypatch.setattr(judgment, "_decide_chunk", fake_chunk)
    questions = {"q{}".format(i): SimpleNamespace() for i in range(95)}
    answers = judgment.decide_batch({"shared": "state"}, questions)

    assert sizes == [40, 40, 15]          # defensive cap: 3 requests, not 95
    assert len(states) == 3               # shared state sent with each chunk
    assert len(answers) == 95
    assert answers["q0"].noul == 0.9 and answers["q94"].noul == 0.9


def test_decide_batch_is_all_or_nothing(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    def fake_chunk(state, chunk, timeout):
        if len(chunk) == 15:              # the last chunk fails
            return None
        return {qid: SimpleNamespace(noul=0.9) for qid in chunk}

    monkeypatch.setattr(judgment, "_decide_chunk", fake_chunk)
    questions = {"q{}".format(i): SimpleNamespace() for i in range(95)}
    assert judgment.decide_batch({}, questions) is None


@pytest.mark.parametrize("error", [
    judgment.TypeSafeError("api down"),
    RuntimeError("unexpected non-sdk error"),
])
def test_decide_batch_fails_open_on_errors(monkeypatch, error):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    class ExplodingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            raise error

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(judgment, "TypeSafeClient", ExplodingClient)
    assert judgment.decide_batch({"s": 1}, {"q": SimpleNamespace()}) is None


def test_decide_chunk_reports_incomplete_batch_as_failure(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    class FakeResponse:
        answers = {}                      # nothing answered

        class usage:
            input_tokens = 0

    class FakeClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def system_one(self, state, questions):
            return FakeResponse

    monkeypatch.setattr(judgment, "TypeSafeClient", FakeClient)
    assert judgment._decide_chunk({}, {"q": SimpleNamespace()}, 5.0) is None


# ── semantic_keep_batch ─────────────────────────────────────────────────────
def test_semantic_keep_batch_disabled_returns_none(monkeypatch):
    monkeypatch.delenv("JEV_SEMANTIC_KEYWORDS", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert judgment.semantic_keep_batch(["fuel"], ["SHELL"]) is None


def test_semantic_keep_batch_empty_inputs(monkeypatch):
    _set_feature_on(monkeypatch)
    assert judgment.semantic_keep_batch([], ["SHELL"]) is None
    assert judgment.semantic_keep_batch(["fuel"], []) is None


def test_semantic_keep_batch_threshold_and_alignment(monkeypatch):
    _set_feature_on(monkeypatch)
    captured = {}

    def fake_decide(state, questions, timeout=judgment.DEFAULT_TIMEOUT):
        captured["state"] = state
        captured["question_ids"] = sorted(questions)
        return {
            "line_0": SimpleNamespace(noul=0.9),   # ≥ 0.75 → keep
            "line_1": SimpleNamespace(noul=0.5),   # < 0.75 → drop
            "line_2": SimpleNamespace(noul=None),  # no usable probability
        }

    monkeypatch.setattr(judgment, "decide_batch", fake_decide)
    verdicts = judgment.semantic_keep_batch(
        ["fuel"], ["SHELL GSTATIONS LONDON", "AMAZON MKT", "TFL TRAVEL"])

    assert verdicts == [True, False, None]
    assert captured["state"]["categories"] == ["fuel"]
    assert captured["state"]["lines"][0] == {"n": 0, "description": "SHELL GSTATIONS LONDON"}
    assert captured["question_ids"] == ["line_0", "line_1", "line_2"]


def test_semantic_keep_batch_custom_threshold(monkeypatch):
    _set_feature_on(monkeypatch)
    monkeypatch.setattr(
        "judgment.decide_batch",
        lambda s, q, timeout=20: {"line_0": SimpleNamespace(noul=0.6)})
    assert judgment.semantic_keep_batch(["fuel"], ["SHELL"], threshold=0.5) == [True]
    assert judgment.semantic_keep_batch(["fuel"], ["SHELL"], threshold=0.75) == [False]


def test_semantic_keep_batch_fail_open(monkeypatch):
    _set_feature_on(monkeypatch)
    monkeypatch.setattr(judgment, "decide_batch", lambda s, q, timeout=20: None)
    assert judgment.semantic_keep_batch(["fuel"], ["SHELL"]) is None


# ── classify_person_like ────────────────────────────────────────────────────
def test_classify_person_like_person_verdicts_only(monkeypatch):
    import fitz

    _set_feature_on(monkeypatch, "JEV_PII_DISAMBIGUATION")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 300), "SHELL LONDON")
    page.insert_text((50, 320), "Mr John Smith")
    rects_shell = page.search_for("SHELL LONDON")
    rects_smith = page.search_for("Mr John Smith")
    findings = [
        {"entity_type": "PERSON", "score": 0.8, "bbox": tuple(rects_shell[0]), "page_number": 0},
        {"entity_type": "PERSON", "score": 0.8, "bbox": tuple(rects_smith[0]), "page_number": 0},
    ]

    def fake_decide(state, questions, timeout=judgment.DEFAULT_TIMEOUT):
        # The page text must have been read into the candidate state.
        assert state["candidates"][0]["text"] == "SHELL LONDON"
        assert state["candidates"][1]["text"] == "Mr John Smith"
        return {
            "cand_0": SimpleNamespace(choice="merchant", confidence=0.9),
            "cand_1": SimpleNamespace(choice="person", confidence=0.9),
        }

    monkeypatch.setattr(judgment, "decide_batch", fake_decide)
    assert judgment.classify_person_like(page, findings) == [1]


def test_classify_person_like_low_confidence_person_dropped(monkeypatch):
    import fitz

    _set_feature_on(monkeypatch, "JEV_PII_DISAMBIGUATION")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 300), "Jane Smith")
    findings = [{"entity_type": "PERSON", "score": 0.8,
                 "bbox": tuple(page.search_for("Jane Smith")[0]), "page_number": 0}]
    monkeypatch.setattr(
        "judgment.decide_batch",
        lambda s, q, timeout=20: {"cand_0": SimpleNamespace(choice="person", confidence=0.3)})
    assert judgment.classify_person_like(page, findings) == []


def test_classify_person_like_disabled(monkeypatch):
    monkeypatch.delenv("JEV_PII_DISAMBIGUATION", raising=False)
    assert judgment.classify_person_like(None, [{"bbox": (0, 0, 1, 1)}]) == []


def test_classify_person_like_caps_candidates(monkeypatch):
    import fitz

    _set_feature_on(monkeypatch, "JEV_PII_DISAMBIGUATION")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 300), "A B")
    bbox = tuple(page.search_for("A B")[0])
    findings = [{"entity_type": "PERSON", "score": 0.8, "bbox": bbox, "page_number": 0}
                for _ in range(30)]
    seen = {}

    def fake_decide(state, questions, timeout=judgment.DEFAULT_TIMEOUT):
        seen["n"] = len(questions)
        return {"cand_{}".format(i): SimpleNamespace(choice="person", confidence=0.9)
                for i in range(len(questions))}

    monkeypatch.setattr(judgment, "decide_batch", fake_decide)
    result = judgment.classify_person_like(page, findings)
    assert seen["n"] == judgment.MAX_PII_CANDIDATES_PER_PAGE
    assert len(result) == judgment.MAX_PII_CANDIDATES_PER_PAGE


# ── pick_header_line / assign_header_roles ──────────────────────────────────
def _feature_generic_rows(monkeypatch):
    _set_feature_on(monkeypatch, "JEV_GENERIC_ROWS")


def test_pick_header_line_maps_choice_to_index(monkeypatch):
    _feature_generic_rows(monkeypatch)
    candidates = [(0, "Opening balance 100.00"), (1, "When Who to How much")]
    monkeypatch.setattr(
        "judgment.decide_batch",
        lambda s, q, timeout=20: {"header": SimpleNamespace(choice="line_1", confidence=0.9)})
    assert judgment.pick_header_line(candidates) == 1


def test_pick_header_line_rejects_no_header_and_low_confidence(monkeypatch):
    _feature_generic_rows(monkeypatch)
    candidates = [(0, "When Who to How much")]
    monkeypatch.setattr(
        "judgment.decide_batch",
        lambda s, q, timeout=20: {"header": SimpleNamespace(choice="no_header", confidence=0.9)})
    assert judgment.pick_header_line(candidates) is None
    monkeypatch.setattr(
        "judgment.decide_batch",
        lambda s, q, timeout=20: {"header": SimpleNamespace(choice="line_0", confidence=0.2)})
    assert judgment.pick_header_line(candidates) is None
    monkeypatch.setattr(
        "judgment.decide_batch",
        lambda s, q, timeout=20: {"header": SimpleNamespace(choice="garbage", confidence=0.9)})
    assert judgment.pick_header_line(candidates) is None


def test_pick_header_line_disabled(monkeypatch):
    monkeypatch.delenv("JEV_GENERIC_ROWS", raising=False)
    assert judgment.pick_header_line([(0, "When")]) is None


def test_assign_header_roles_maps_phrases(monkeypatch):
    _feature_generic_rows(monkeypatch)

    def fake_decide(state, questions, timeout=judgment.DEFAULT_TIMEOUT):
        assert [c["text"] for c in state["line"]] == ["When", "Who to", "How much"]
        return {
            "phrase_0": SimpleNamespace(choice="date"),
            "phrase_1": SimpleNamespace(choice="description"),
            "phrase_2": SimpleNamespace(choice="amount"),
        }

    monkeypatch.setattr(judgment, "decide_batch", fake_decide)
    assert judgment.assign_header_roles(["When", "Who to", "How much"]) == \
        ["date", "description", "amount"]


def test_assign_header_roles_disabled(monkeypatch):
    monkeypatch.delenv("JEV_GENERIC_ROWS", raising=False)
    assert judgment.assign_header_roles(["When"]) is None


# ── Privacy: statement text never reaches the logs ─────────────────────────
def test_no_statement_text_in_logs(monkeypatch, caplog):
    _set_feature_on(monkeypatch)

    class FakeClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def system_one(self, state, questions):
            return SimpleNamespace(
                answers={qid: SimpleNamespace(noul=0.9) for qid in questions},
                usage=SimpleNamespace(input_tokens=42),
            )

    monkeypatch.setattr(judgment, "TypeSafeClient", FakeClient)
    with caplog.at_level(logging.DEBUG, logger="judgment"):
        assert judgment.semantic_keep_batch(
            ["fuel"], ["ZEBRA-SECRET-MERCHANT LONDON WC2"]) == [True]

    messages = [r.getMessage() for r in caplog.records]
    assert messages, "expected at least the batch-ok log line"
    assert any("jev batch ok" in m for m in messages)
    for m in messages:
        assert "ZEBRA-SECRET-MERCHANT" not in m
        assert "LONDON" not in m
