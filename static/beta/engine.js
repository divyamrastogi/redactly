/**
 * Redactly browser engine — a faithful JS port of redact_bank_generic.py
 * (the layout-driven generic bank engine), running on MuPDF compiled to
 * WebAssembly (the same C library the server uses via PyMuPDF).
 *
 * Ported scope (beta): column detection, dated + dateless (Wise-style)
 * transaction groups, Keep/Redact matching direction, landlord mode
 * (keep credits), redact balances. NOT ported (server-only): the TypeSafe
 * Jev judgment layer and the Presidio PII pass.
 *
 * Runs against an already-opened mupdf.js PDFDocument. All coordinates are
 * PDF points; every span/row/column structure mirrors the Python original.
 */

// ── Patterns ────────────────────────────────────────────────────────────────
// Row dates: "1 May" / "01 May" (D MMM), "01/05/2026" (DD/MM/YYYY), "01/05/26"
const DATE_PATTERNS = [
    /^\d{1,2}\s+[A-Za-z]{3}(?:\s+\d{2}|\s+\d{4})?$/,
    /^\d{1,2}\/\d{1,2}\/\d{4}$/,
    /^\d{1,2}\/\d{1,2}\/\d{2}$/,
];

// Rows that must always survive regardless of keywords.
const ALWAYS_KEEP_RE = /balance\s+(brought|carried)\s+forward/i;

// Amounts: "1,234.56", "£1,234.56", "1,234.56CR", "-12.34"
const AMOUNT_RE = /^-?£?\s?[\d,]+\.\d{2}\s*(?:CR)?$/i;

// Column-header labels → role (order matters; checked top-to-bottom).
const ROLE_LABELS = [
    ['balance', ['balance']],
    ['paid_out', ['paid out', 'money out', 'withdrawal', 'debit', 'payment out', 'payments out', 'outgoing']],
    ['paid_in', ['paid in', 'money in', 'receipt', 'deposit', 'credit', 'incoming']],
    ['date', ['date']],
    ['description', ['description', 'details', 'narrative', 'transaction', 'payee', 'memo']],
    ['amount', ['amount']],
];

const ROW_Y_TOLERANCE = 3;

// Longer labels that only ever match EXACTLY (never by containment).
const EXACT_LABELS = {
    'payment type and details': 'description',   // HSBC
};

const strip = (s) => s.trim();
const squeeze = (s) => strip(s).toLowerCase().split(/\s+/).join(' ');
const deSpace = (s) => s.replace(/[^a-z0-9]/g, '');

// ── Header / column detection ───────────────────────────────────────────────
function headerRole(text) {
    const t = squeeze(text);
    if (!t) return null;
    const tNs = deSpace(t);
    for (const [lbl, role] of Object.entries(EXACT_LABELS)) {
        if (tNs === deSpace(lbl)) return role;
    }
    if (t.length > 25 || t.split(' ').length > 3) return null;
    for (const [role, labels] of ROLE_LABELS) {
        for (const lbl of labels) {
            const re = new RegExp(`\\b${lbl.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`);
            if (re.test(t) || tNs === lbl.replace(/ /g, '')) return role;
        }
    }
    return null;
}

// Merge adjacent spans on one baseline into phrases (x-gap <= gap pt).
function mergeLinePhrases(lineSpans, gap = 12) {
    const phrases = [];
    let cur = null;
    for (const s of [...lineSpans].sort((a, b) => a.bbox[0] - b.bbox[0])) {
        if (cur !== null && s.bbox[0] - cur.bbox[2] <= gap) {
            cur = {
                text: cur.text + ' ' + s.text,
                bbox: [cur.bbox[0], Math.min(cur.bbox[1], s.bbox[1]),
                       s.bbox[2], Math.max(cur.bbox[3], s.bbox[3])],
            };
        } else {
            if (cur !== null) phrases.push(cur);
            cur = { text: s.text, bbox: [...s.bbox] };
        }
    }
    if (cur !== null) phrases.push(cur);
    return phrases;
}

// Group spans into rows by baseline Y, top-to-bottom.
function clusterRows(spans, tol = ROW_Y_TOLERANCE) {
    const rows = [];
    for (const s of [...spans].sort((a, b) => (a.bbox[1] - b.bbox[1]) || (a.bbox[0] - b.bbox[0]))) {
        const y = s.bbox[1];
        let hit = false;
        for (const r of rows) {
            if (Math.abs(r.y0 - y) <= tol) { r.spans.push(s); hit = true; break; }
        }
        if (!hit) rows.push({ y0: y, spans: [s] });
    }
    return rows;
}

function headerCandidates(lineSpans) {
    const cands = [];
    for (const phrase of mergeLinePhrases(lineSpans)) {
        const role = headerRole(phrase.text);
        if (role) cands.push([phrase, role]);
    }
    return cands;
}

// >= 3 distinct roles, incl. a date/description column and one money column.
function credibleHeader(cands) {
    const roles = new Set(cands.map(([, role]) => role));
    return roles.size >= 3
        && (roles.has('date') || roles.has('description'))
        && (roles.has('paid_in') || roles.has('paid_out') || roles.has('balance') || roles.has('amount'));
}

function columnsFromHeaderLine(headerLine, pageWidth) {
    const headerY = Math.min(...headerLine.map(([p]) => p.bbox[3]));

    const centres = headerLine
        .map(([sp, role]) => [(sp.bbox[0] + sp.bbox[2]) / 2.0, role])
        .sort((a, b) => a[0] - b[0]);
    const xs = centres.map(([c]) => c);
    const roles = centres.map(([, r]) => r);
    const bounds = [0.0];
    for (let i = 0; i < xs.length - 1; i++) bounds.push((xs[i] + xs[i + 1]) / 2.0);
    bounds.push(pageWidth);

    const columns = roles.map((role, i) => ({ role, x0: bounds[i], x1: bounds[i + 1] }));

    // Ambiguous 'amount' column: rightmost with no explicit balance column and
    // separate in/out columns = the running balance (Wise); otherwise paid_out.
    const roleSet = new Set(columns.map((c) => c.role));
    columns.forEach((col, i) => {
        if (col.role !== 'amount') return;
        if (i === columns.length - 1 && !roleSet.has('balance')
                && (roleSet.has('paid_in') || roleSet.has('paid_out'))) {
            col.role = 'balance';
        } else {
            col.role = 'paid_out';
        }
    });

    return [columns, headerY];
}

function detectColumns(spans, pageWidth) {
    let best = null;
    for (const line of clusterRows(spans, 5)) {
        const cands = headerCandidates(line.spans);
        if (credibleHeader(cands)) {
            if (best === null || cands.length > best.length) best = cands;
        }
    }
    if (best === null) return [null, null];
    return columnsFromHeaderLine(best, pageWidth);
}

// ── Span helpers ────────────────────────────────────────────────────────────
// Collect text spans from a mupdf.js page. asJSON() gives line-granular
// entries, each with a single font — the same granularity PyMuPDF's
// get_text("dict") spans provide on these statements.
function collectSpans(page) {
    const spans = [];
    const json = JSON.parse(page.toStructuredText().asJSON());
    for (const block of json.blocks ?? []) {
        for (const line of block.lines ?? []) {
            const text = strip(line.text ?? '');
            if (!text) continue;
            const { x, y, w, h } = line.bbox;
            spans.push({ text, bbox: [x, y, x + w, y + h] });
        }
    }
    return spans;
}

function assignRole(span, columns) {
    const cx = (span.bbox[0] + span.bbox[2]) / 2.0;
    for (const col of columns) {
        if (col.x0 <= cx && cx < col.x1) return col.role;
    }
    return cx < columns[0].x0 ? columns[0].role : columns[columns.length - 1].role;
}

// Redaction rect: padded horizontally, INSET vertically — consecutive rows'
// span boxes overlap on tightly-leaded statements, and MuPDF removes every
// character whose box merely intersects a redaction rect (same rule PyMuPDF
// wraps). The vertical core still catches every glyph of the target row
// without reaching the neighbours.
function padRect(bbox, padX = 1.0) {
    const [x0, y0, x1, y1] = bbox;
    const inset = (y1 - y0) * 0.25;
    return [x0 - padX, y0 + inset, x1 + padX, y1 - inset];
}

function matchesDate(text) {
    const t = strip(text);
    return DATE_PATTERNS.some((re) => re.test(t));
}

function isAmount(text) {
    return AMOUNT_RE.test(strip(text));
}

function parseAmount(text) {
    const s = strip(text).replace(/£/g, '').replace(/,/g, '').toUpperCase().replace(/CR/g, '').trim();
    const v = parseFloat(s);
    return Number.isFinite(v) ? Math.abs(v) : 0.0;
}

function isDateCell(cell, dateColumnKnown) {
    if (!matchesDate(cell.text)) return false;
    return !dateColumnKnown || cell.role === 'date';
}

// Best-effort amount for a kept row: prefer paid-out, else paid-in.
function rowAmount(cells) {
    for (const role of ['paid_out', 'paid_in']) {
        for (const c of cells) {
            if (c.role === role && isAmount(c.text)) return parseAmount(c.text);
        }
    }
    return 0.0;
}

function amountCells(cells) {
    return cells.filter((c) => (c.role === 'paid_in' || c.role === 'paid_out') && isAmount(c.text));
}

function isCredit(cells, columns) {
    const hasPaidIn = columns.some((c) => c.role === 'paid_in');
    const hasPaidOut = columns.some((c) => c.role === 'paid_out');
    if (hasPaidIn && hasPaidOut) return amountCells(cells).some((c) => c.role === 'paid_in');
    for (const c of amountCells(cells)) {
        const t = strip(c.text);
        if (t.toUpperCase().endsWith('CR') || t.startsWith('-')) return true;
    }
    return false;
}

// ── Per-page redaction ──────────────────────────────────────────────────────
function redactBalanceAmounts(page, rows, columns, headerY, addRedaction) {
    for (const row of rows) {
        for (const s of row.spans) {
            if (!isAmount(s.text)) continue;
            if (row.y0 <= headerY || assignRole(s, columns) === 'balance') {
                addRedaction(page, padRect(s.bbox));
            }
        }
    }
}

// Wise-style dateless transaction table (no Date column).
function redactDateless(page, rows, columns, headerY, opts, state, addRedaction) {
    const TAIL_GAP = 20;
    const { keepKeywords, keepCredits, direction } = opts;

    const groups = [];
    let current = null;
    for (const row of rows) {
        if (row.y0 <= headerY) continue;
        const cells = row.spans.map((s) => ({ ...s, role: assignRole(s, columns) }));
        const hasMoney = cells.some((c) => (c.role === 'paid_in' || c.role === 'paid_out') && isAmount(c.text));
        if (hasMoney) {
            current = { cells: [...cells], head: cells, lastY: row.y0 };
            groups.push(current);
        } else if (current !== null && row.y0 - current.lastY <= TAIL_GAP) {
            current.cells.push(...cells);
            current.lastY = row.y0;
        } else {
            current = null; // detached row: furniture/boilerplate — untouched
        }
    }

    const descriptions = groups.map((g) =>
        g.cells.filter((c) => c.role === 'description').map((c) => c.text).join(' ').trim());

    for (let gi = 0; gi < groups.length; gi++) {
        const g = groups[gi];
        const description = descriptions[gi];
        const isKw = keepKeywords.length > 0
            && keepKeywords.some((kw) => description.toLowerCase().includes(kw.toLowerCase()));
        const credit = keepCredits ? isCredit(g.head, columns) : false;
        const amount = rowAmount(g.head);

        let keepRow;
        if (keepKeywords.length > 0) {
            keepRow = (direction === 'keep' ? isKw : !isKw) || credit;
        } else {
            keepRow = !keepCredits;
        }
        if (credit) keepRow = true;

        if (keepRow) {
            state.keptRows.push({ description, amount, page: state.pageNum });
        } else {
            state.redacted++;
            for (const c of g.cells) {
                if (['date', 'description', 'paid_in', 'paid_out'].includes(c.role)) {
                    addRedaction(page, padRect(c.bbox));
                }
            }
        }
    }
}

// One page. Returns the detected header_y (or null when the page is untouched).
function redactPage(page, opts, state, pageWidth, addRedaction) {
    const { keepKeywords, keepCredits, redactBalances, direction } = opts;
    const spans = collectSpans(page);
    const [columns, headerY] = detectColumns(spans, pageWidth);
    if (!columns) {
        state.pageReports.push({ page: state.pageNum, headerFound: false });
        return null;
    }

    const dateColumnKnown = columns.some((c) => c.role === 'date');
    const rows = clusterRows(spans);

    if (!dateColumnKnown) {
        redactDateless(page, rows, columns, headerY, opts, state, addRedaction);
        if (redactBalances) redactBalanceAmounts(page, rows, columns, headerY, addRedaction);
        return headerY;
    }

    // Dated layouts: a group starts at a dated row and absorbs close-by
    // dateless rows underneath (wrapped descriptions — HSBC puts the amount
    // on such a continuation line).
    const TAIL_GAP = 20;
    const groups = [];
    let current = null;
    for (const row of rows) {
        if (row.y0 <= headerY) continue; // header line or furniture above it
        const cells = row.spans.map((s) => ({ ...s, role: assignRole(s, columns) }));
        if (cells.some((c) => isDateCell(c, dateColumnKnown))) {
            current = { cells: [...cells], lastY: row.y0 };
            groups.push(current);
        } else if (current !== null && row.y0 - current.lastY <= TAIL_GAP) {
            current.cells.push(...cells);
            current.lastY = row.y0;
        } else {
            current = null; // detached row: totals/furniture — untouched
        }
    }

    const descriptions = groups.map((g) =>
        g.cells.filter((c) => c.role === 'description').map((c) => c.text).join(' ').trim());

    for (let gi = 0; gi < groups.length; gi++) {
        const g = groups[gi];
        const description = descriptions[gi];
        if (ALWAYS_KEEP_RE.test(description)) continue; // structure, not spend
        const isKw = keepKeywords.length > 0
            && keepKeywords.some((kw) => description.toLowerCase().includes(kw.toLowerCase()));
        const credit = keepCredits ? isCredit(g.cells, columns) : false;
        const amount = rowAmount(g.cells);

        let keepRow;
        if (keepKeywords.length > 0) {
            keepRow = (direction === 'keep' ? isKw : !isKw) || credit;
        } else {
            keepRow = !keepCredits;
        }
        if (credit) keepRow = true;

        if (keepRow) {
            state.keptRows.push({ description, amount, page: state.pageNum });
        } else {
            state.redacted++;
            for (const c of g.cells) {
                if (['date', 'description', 'paid_in', 'paid_out'].includes(c.role)) {
                    addRedaction(page, padRect(c.bbox));
                }
            }
        }
    }

    if (redactBalances) redactBalanceAmounts(page, rows, columns, headerY, addRedaction);
    state.pageReports.push({ page: state.pageNum, headerFound: true });
    return headerY;
}

// ── Public entry point ──────────────────────────────────────────────────────
/**
 * Redact a bank statement entirely in memory.
 *
 * @param {import('mupdf').PDFDocument} doc  an opened mupdf.js PDFDocument
 * @param {object} opts
 * @param {string[]} opts.keywords        match keywords (case-insensitive substring)
 * @param {boolean} [opts.keepCredits]    landlord mode: always keep credit rows
 * @param {boolean} [opts.redactBalances] remove summary + running-balance amounts
 * @param {'keep'|'redact'} [opts.direction] 'keep' = whitelist matches,
 *        'redact' = blacklist matches (mirrors the server toggle)
 * @returns {{ total: number, keptRows: Array<{description: string, amount: number, page: number}>, redacted: number, pageReports: Array }}
 */
export function redactBankGeneric(doc, opts = {}) {
    const {
        keywords = [],
        keepCredits = false,
        redactBalances = false,
        direction = 'keep',
    } = opts;

    const state = { keptRows: [], redacted: 0, pageReports: [], pageNum: 0 };

    const addRedaction = (page, rect) => {
        const annot = page.createAnnotation('Redact');
        annot.setRect(rect);
        annot.update();
    };

    for (let i = 0; i < doc.countPages(); i++) {
        state.pageNum = i;
        const page = doc.loadPage(i);
        redactPage(page, { keepKeywords: keywords, keepCredits, redactBalances, direction },
            state, page.getBounds()[2], addRedaction);
        // Defaults mirror PyMuPDF apply_redactions(images=2, graphics=1, text=0)
        page.applyRedactions(
            true,
            2 /* REDACT_IMAGE_PIXELS */,
            1 /* REDACT_LINE_ART_REMOVE_IF_COVERED */,
            0 /* REDACT_TEXT_REMOVE */);
    }

    const total = state.keptRows.reduce((acc, r) => acc + (r.amount || 0), 0);
    return { total, keptRows: state.keptRows, redacted: state.redacted, pageReports: state.pageReports };
}

/** Render one page to a PNG Uint8Array for in-browser preview. */
export function renderPagePng(doc, pageNum, scale = 1.5) {
    const mupdf = renderPagePng._mupdf;
    const page = doc.loadPage(pageNum);
    const pix = page.toPixmap(mupdf.Matrix.scale(scale, scale), mupdf.ColorSpace.DeviceRGB);
    const png = pix.asPNG();
    pix.destroy?.();
    return png;
}

/** Allow the harness/page to inject the mupdf module (avoids a circular import). */
export function setMupdfModule(mod) {
    renderPagePng._mupdf = mod;
}
