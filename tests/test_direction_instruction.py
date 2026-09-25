"""Tests for the direction toggle (keep/redact matching) and the plain-English
instruction mode.

Pins the same contracts as the Jev work, from both sides:

- default (direction='keep', no instruction) is byte-identical to the
  historic whitelist behaviour;
- direction='redact' inverts the predicate WITHOUT touching structural
  guarantees (balances never redacted, page furniture untouched);
- instruction matches always redact, in either direction, but a landlord
  credit still wins;
- fail-open inversion: when the Jev batch fails, the redact direction and
  the instruction mode degrade to exact substring only — deletions are
  never guessed;
- server validation and UI gating (instruction box hidden when the flag is
  off); instruction text never reaches analytics.
"""
import io
import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import judgment
from redact_bank_generic import redact_bank_generic
from redact_generic import redact_pdf_generic
from test_bank_generic import build_statement


def _jev_env(monkeypatch, instructions=True):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    if instructions:
        monkeypatch.setenv("JEV_INSTRUCTIONS", "on")
    else:
        monkeypatch.delenv("JEV_INSTRUCTIONS", raising=False)


# ── Direction flip: bank engine ─────────────────────────────────────────────
def test_redact_direction_flips_predicate_bank_engine(tmp_path):
    build_statement(tmp_path / "in.pdf")
    out = tmp_path / "out.pdf"

    _, total, kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(out), ["Tesco"], direction="redact")

    names = sorted(r["description"] for r in kept)
    assert names == ["Amazon", "Landlord Rent", "Netflix", "Payroll", "Pret A Manger"]
    assert total == pytest.approx(2000.00 + 8.75 + 1200.00 + 12.99 + 33.50)

    text = fitz.open(str(out))[0].get_text()
    assert "Tesco" not in text and "45.20" not in text      # matched row gone
    assert "Netflix" in text and "33.50" in text            # the rest survives
    assert "2454.80" in text                                # balances never touched
    assert "Sort Code 12-34-56" in text and "Jane Smith" in text  # furniture


def test_keep_direction_is_the_historic_default(tmp_path):
    build_statement(tmp_path / "in.pdf")

    _, total_default, kept_default = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(tmp_path / "a.pdf"), ["rent"])
    _, total_explicit, kept_explicit = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(tmp_path / "b.pdf"), ["rent"], direction="keep")

    assert total_default == total_explicit == pytest.approx(1200.00)
    assert kept_default == kept_explicit


# ── Instruction mode: bank engine ───────────────────────────────────────────
def test_instruction_redacts_in_keep_direction(tmp_path, monkeypatch):
    _jev_env(monkeypatch)
    build_statement(tmp_path / "in.pdf")
    monkeypatch.setattr(
        judgment, "semantic_instruction_batch",
        lambda instruction, descriptions, threshold=judgment.KEYWORD_THRESHOLD:
            [d == "Netflix" for d in descriptions])

    # No keywords at all — the instruction alone decides.
    _, total, kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf"), [],
        instruction="streaming subscriptions")

    assert sorted(r["description"] for r in kept) == \
        ["Amazon", "Landlord Rent", "Payroll", "Pret A Manger", "Tesco"]
    assert total == pytest.approx(2000.00 + 45.20 + 8.75 + 1200.00 + 33.50)
    assert "Netflix" not in fitz.open(str(tmp_path / "out.pdf"))[0].get_text()


def test_landlord_credit_beats_instruction(tmp_path, monkeypatch):
    _jev_env(monkeypatch)
    build_statement(tmp_path / "in.pdf")
    # Payroll (+£2000 paid in) and Netflix both match the instruction; only
    # Netflix may be redacted — the credit row is landlord proof of income.
    monkeypatch.setattr(
        judgment, "semantic_instruction_batch",
        lambda instruction, descriptions, threshold=judgment.KEYWORD_THRESHOLD:
            [d in ("Payroll", "Netflix") for d in descriptions])

    _, total, kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf"), [],
        keep_credits=True, instruction="money coming in and subscriptions")

    names = sorted(r["description"] for r in kept)
    assert "Payroll" in names                    # credit wins
    assert "Netflix" not in names                # instruction redaction applied
    assert "2000.00" in fitz.open(str(tmp_path / "out.pdf"))[0].get_text()


def test_instruction_fail_open_redacts_nothing_extra(tmp_path, monkeypatch):
    build_statement(tmp_path / "in.pdf")
    baseline = tmp_path / "baseline.pdf"
    jev = tmp_path / "jev.pdf"

    _, b_total, b_kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(baseline), [])

    _jev_env(monkeypatch)
    monkeypatch.setattr(judgment, "semantic_instruction_batch",
                        lambda *a, **k: None)   # API failure
    _, j_total, j_kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(jev), [], instruction="everything")

    assert b_kept == j_kept and b_total == j_total  # no extra deletions
    assert fitz.open(str(baseline))[0].get_text() == \
        fitz.open(str(jev))[0].get_text()


def test_redact_direction_with_instruction_fail_open(tmp_path, monkeypatch):
    build_statement(tmp_path / "in.pdf")
    _jev_env(monkeypatch)
    # Both Jev batches fail: 'redact' must still work via exact substring only.
    monkeypatch.setattr(judgment, "semantic_keep_batch", lambda *a, **k: None)
    monkeypatch.setattr(judgment, "semantic_instruction_batch", lambda *a, **k: None)

    _, total, kept = redact_bank_generic(
        str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf"), ["Tesco"],
        direction="redact", instruction="gambling")

    names = sorted(r["description"] for r in kept)
    assert "Tesco" not in names                       # exact keyword still applied
    assert len(kept) == 5
    assert total == pytest.approx(3255.24)


# ── Direction flip: card engine ─────────────────────────────────────────────
def _build_card_statement(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 80), "Transactions")
    for y, desc, amount in [(120, "SHELL", "£10.00"), (150, "NETFLIX", "£12.99")]:
        page.insert_text((50, y), desc)
        page.insert_text((300, y), amount)
    doc.save(str(path))
    doc.close()


def test_card_engine_redact_direction(tmp_path):
    _build_card_statement(tmp_path / "card.pdf")

    # Historic behaviour: keep NETFLIX, redact the rest.
    _, total_keep = redact_pdf_generic(
        str(tmp_path / "card.pdf"), ["NETFLIX"], "keep.pdf")
    assert total_keep == pytest.approx(12.99)

    # Inverted: redact NETFLIX, keep the rest — total now sums the survivors.
    _, total_redact = redact_pdf_generic(
        str(tmp_path / "card.pdf"), ["NETFLIX"], "redact.pdf", direction="redact")
    assert total_redact == pytest.approx(10.00)

    text = fitz.open(str(tmp_path / "redact_10.00.pdf"))[0].get_text()
    assert "NETFLIX" not in text and "12.99" not in text
    assert "SHELL" in text and "10.00" in text


# ── Server plumbing ─────────────────────────────────────────────────────────
@pytest.fixture()
def client():
    import app as app_module
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _post(client, **fields):
    fields.setdefault("pdf", (io.BytesIO(b"%PDF-1.4 test"), "a.pdf"))
    return client.post("/redact", data=fields, content_type="multipart/form-data")


def test_invalid_direction_returns_400(client):
    resp = _post(client, keywords="rent", direction="sideways")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Invalid direction"


def test_instruction_requires_server_flag(client, monkeypatch):
    _jev_env(monkeypatch, instructions=False)
    resp = _post(client, keywords="rent", instruction="gambling")
    assert resp.status_code == 400
    assert "not enabled" in resp.get_json()["error"]


def test_instruction_replaces_keywords_and_threads_through(client, monkeypatch):
    import app as app_module
    _jev_env(monkeypatch)
    captured = {}

    def fake_process(file, keywords, provider, enhanced_privacy, mode='custom',
                     keep_credits=False, direction='keep', instruction=None):
        captured.update(keywords=keywords, direction=direction,
                        instruction=instruction)
        return ("redacted_a_£1.00.pdf", 1.0, 1, True, "generic_bank_uk", False)

    monkeypatch.setattr(app_module, "process_single_file", fake_process)
    monkeypatch.setattr(app_module, "update_usage_counter", lambda: 1)

    resp = _post(client, keywords="", instruction="gambling", direction="redact")
    assert resp.status_code == 200
    assert captured == {"keywords": [], "direction": "redact",
                        "instruction": "gambling"}
    assert resp.get_json()["semantic_enhanced"] is True


# ── UI gating ───────────────────────────────────────────────────────────────
def test_direction_toggle_always_present_instruction_box_gated(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="direction-group"' in html            # plain feature, no AI
    assert 'id="instruction"' not in html            # Jev-gated box hidden
    assert "Keep matching" in html and "Redact matching" in html


def test_instruction_box_shown_when_flag_on(client, monkeypatch):
    _jev_env(monkeypatch)
    html = client.get("/").get_data(as_text=True)
    assert 'id="instruction"' in html
    assert "describe what to redact" in html


def test_track_calls_never_carry_instruction_text(client, monkeypatch):
    _jev_env(monkeypatch)
    html = client.get("/").get_data(as_text=True)
    assert "track('instruction_used')" in html       # flag only, no content arg
