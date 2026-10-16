/**
 * Parity harness: runs the browser engine (static/beta/engine.js) in Node on
 * the real-statement fixtures and compares kept rows against the Python
 * engine's output (redact_bank_generic.py).
 *
 * Usage:  npm i --no-save mupdf && node tests/browser/parity.mjs
 *         MUPDF_PKG=/abs/path/node_modules/mupdf node tests/browser/parity.mjs
 *
 * Expected values are the live Python results (landlord mode + no-match),
 * matching tests/test_e2e_real_statements.py's invariants.
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));

let mupdf;
if (process.env.MUPDF_PKG) {
    mupdf = await import(pathToFileURL(path.join(process.env.MUPDF_PKG, 'dist', 'mupdf.js')).href);
} else {
    mupdf = await import('mupdf');
}

const { redactBankGeneric, setMupdfModule } = await import(
    pathToFileURL(path.join(here, '..', '..', 'static', 'beta', 'engine.js')).href);
setMupdfModule(mupdf);

const FIXTURES_DIR = path.join(here, '..', 'e2e_statements');

// Python ground truth (redact_bank_generic on the same fixtures):
//   landlord keep_credits=True and nomatch keywords=['zzz-e2e-never-matches'].
const EXPECTED = {
    hsbc: { landlord: [[120.0, 'CR HENGAMEH MOHAMMADK parking Ju'], [25.46, 'CR VITALITY'], [5383.83, 'CR CLEARWATER ANALYTI'], [145.0, 'CR DENPLAN LTD 02624836'], [2.72, 'CR Stripe Payments UK STRIPE']], nomatch: 0 },
    wise: { landlord: [[859.05, 'Received money from IGOTANOFFER LTD. with reference IGAO 1 June 2026 Transaction: TRANSFER-2164912564']], nomatch: 0 },
    revolut: { landlord: [[72.19, 'Apple Pay top-up by ***** Reference: From: *5821']], nomatch: 0 },
};

let failures = 0;
const check = (cond, label, detail = '') => {
    console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${cond || !detail ? '' : ` — ${detail}`}`);
    if (!cond) failures++;
};

for (const name of Object.keys(EXPECTED)) {
    const pdfPath = path.join(FIXTURES_DIR, `${name}.pdf`);
    if (!fs.existsSync(pdfPath)) { console.log(`SKIP ${name} (fixture missing)`); continue; }
    const bytes = new Uint8Array(fs.readFileSync(pdfPath));

    const openDoc = () => mupdf.PDFDocument.openDocument(bytes, 'application/pdf');

    // landlord mode
    const land = redactBankGeneric(openDoc(), { keywords: [], keepCredits: true });
    const exp = EXPECTED[name].landlord;
    check(land.keptRows.length === exp.length, `${name} landlord kept count`,
          `js=${land.keptRows.length} py=${exp.length}`);
    check(Math.abs(land.total - exp.reduce((a, [amt]) => a + amt, 0)) < 0.005,
          `${name} landlord total`, `js=£${land.total.toFixed(2)}`);
    for (const [amt, desc] of exp) {
        check(land.keptRows.some((r) => Math.abs(r.amount - amt) < 0.005 && r.description === desc),
              `${name} landlord keeps "${desc.slice(0, 40)}" @ £${amt.toFixed(2)}`,
              `js kept: ${JSON.stringify(land.keptRows.map((r) => [r.amount, r.description]))}`);
    }

    // no-match must keep zero rows
    const nomatch = redactBankGeneric(openDoc(), { keywords: ['zzz-e2e-never-matches'] });
    check(nomatch.keptRows.length === EXPECTED[name].nomatch, `${name} nomatch keeps 0 rows`,
          `js=${nomatch.keptRows.length}`);
}

// redaction actually destroys text: run nomatch on hsbc, re-extract, assert
// every transaction amount span is gone from page 1's account summary area.
{
    const bytes = new Uint8Array(fs.readFileSync(path.join(FIXTURES_DIR, 'hsbc.pdf')));
    const doc = mupdf.PDFDocument.openDocument(bytes, 'application/pdf');
    const out = redactBankGeneric(doc, { keywords: ['zzz-e2e-never-matches'] });
    const buf = doc.saveToBuffer('garbage=compact,compress=yes');
    const doc2 = mupdf.PDFDocument.openDocument(buf.asUint8Array(), 'application/pdf');
    const st = JSON.parse(doc2.loadPage(0).toStructuredText().asJSON());
    const texts = [];
    for (const b of st.blocks ?? []) for (const l of b.lines ?? []) texts.push(l.text ?? '');
    const amountSurvivors = texts.filter((t) => /^-?£?\s?[\d,]+\.\d{2}\s*(?:CR)?$/i.test(t.trim()));
    check(out.redacted > 0, 'hsbc nomatch redacted some rows', `redacted=${out.redacted}`);
    // The Balance column intentionally survives (Python snapshot: p1
    // amount_spans == 16 after nomatch redaction — running balances + summary).
    check(amountSurvivors.length === 16, 'hsbc p1 surviving amount spans match Python snapshot (16)',
          `js=${amountSurvivors.length}: ${JSON.stringify(amountSurvivors)}`);
}

console.log(failures === 0 ? '\nALL PARITY CHECKS PASSED' : `\n${failures} CHECK(S) FAILED`);
process.exit(failures === 0 ? 0 : 1);
