from flask import Flask, request, send_file, after_this_request, render_template_string, jsonify
import os
import logging
from redact_transactions import redact_transactions
from redact_generic import redact_pdf_generic
from redact_financial_details import redact_barclaycard_with_privacy, redact_amex_with_privacy
from redact_barclaycard import redact_barclaycard
from provider_config import get_all_providers

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Function to handle the usage counter
def update_usage_counter():
    counter_file = 'usage_counter.txt'
    try:
        if os.path.exists(counter_file):
            with open(counter_file, 'r+') as f:
                count = int(f.read() or '0') + 1
                f.seek(0)
                f.write(str(count))
                f.truncate()
        else:
            count = 1
            with open(counter_file, 'w') as f:
                f.write(str(count))
        return count
    except Exception as e:
        logger.error(f"Error updating usage counter: {str(e)}")
        return None

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Credit Card Statement Redaction Tool | Whitelist Transactions</title>
    <meta name="description" content="Redact your credit card statements easily. Supports AMEX, Barclaycard, Visa, Mastercard and more.">
    <script src="https://cdn.tailwindcss.com"></script>
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-SY9PXXMVD8"></script>
    <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){dataLayer.push(arguments);}
    gtag('js', new Date());
    gtag('config', 'G-SY9PXXMVD8');
    </script>
</head>
<body class="bg-gray-100 min-h-screen flex flex-col">
    <header class="w-full bg-indigo-600 text-white text-center py-8">
        <h1 class="text-4xl font-bold">Credit Card Statement Redaction Tool</h1>
        <p class="mt-2 text-xl">Whitelist Your Important Transactions</p>
        <p class="mt-1 text-sm text-gray-300">Supports AMEX, Barclaycard, Visa, Mastercard and more</p>
        {% if usage_count %}<p class="mt-2 text-sm">Used {{ usage_count }} times</p>{% endif %}
    </header>

    <main class="flex-grow container mx-auto px-4 py-8 max-w-5xl">
        <div class="flex flex-col md:flex-row gap-8 mb-8">

            <!-- How it works -->
            <section class="bg-white p-8 rounded-lg shadow-md md:w-1/2">
                <h2 class="text-2xl font-bold mb-4 text-gray-800">How It Works</h2>
                <p class="text-gray-600 mb-4">Upload one or more credit card statement PDFs, enter keywords for the transactions you want to keep, and download the redacted files as they finish.</p>
                <ul class="list-disc list-inside text-gray-600 mb-4 space-y-1">
                    <li>Multi-file upload — process several months at once</li>
                    <li>Auto-detects AMEX and Barclaycard</li>
                    <li>Each file appears as soon as it's ready</li>
                    <li>Filename includes the whitelisted total</li>
                </ul>
                <p class="text-gray-600"><strong>Example keywords:</strong> <em>Tfl Travel, Hyperoptic, Your-Saving</em></p>
            </section>

            <!-- Form -->
            <div class="bg-white p-8 rounded-lg shadow-md md:w-1/2">
                <h2 class="text-2xl font-bold mb-6 text-center text-gray-800">Redact Statements</h2>

                <div class="space-y-4">
                    <!-- Provider -->
                    <div>
                        <label class="block text-sm font-medium text-gray-700">Credit Card Provider</label>
                        <select id="provider" class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500">
                            {% for value, display in providers %}
                            <option value="{{ value }}" {% if value == 'auto' %}selected{% endif %}>{{ display }}</option>
                            {% endfor %}
                        </select>
                        <p class="mt-1 text-xs text-gray-500">Auto-detect works for most statements</p>
                    </div>

                    <!-- Barclaycard tip -->
                    <div id="barclaycard-tip" class="hidden bg-blue-50 border border-blue-200 rounded-md p-3 text-sm text-blue-700">
                        <strong>🏦 Barclaycard</strong> — precise row-by-row redaction + financial privacy. Enter merchant keywords.
                    </div>

                    <!-- File picker -->
                    <div>
                        <label class="block text-sm font-medium text-gray-700">PDF Statements <span class="text-gray-400">(one or more)</span></label>
                        <div id="drop-zone"
                             class="mt-1 flex flex-col items-center justify-center border-2 border-dashed border-gray-300 rounded-md px-6 py-8 cursor-pointer hover:border-indigo-400 transition-colors"
                             onclick="document.getElementById('pdf-input').click()">
                            <svg class="w-10 h-10 text-gray-400 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
                                      d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/>
                            </svg>
                            <p class="text-sm text-gray-500">Drop PDFs here or <span class="text-indigo-600 font-medium">browse</span></p>
                            <input id="pdf-input" type="file" accept=".pdf" multiple class="hidden">
                        </div>
                        <!-- Selected files list -->
                        <ul id="file-list" class="mt-2 space-y-1 text-sm text-gray-600"></ul>
                    </div>

                    <!-- Keywords -->
                    <div>
                        <label class="block text-sm font-medium text-gray-700">Keywords to Keep <span class="text-gray-400">(comma-separated)</span></label>
                        <input id="keywords" type="text" placeholder="e.g. Tfl Travel, Hyperoptic, Your-Saving"
                               class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500">
                    </div>

                    <!-- Enhanced privacy -->
                    <div class="flex items-start gap-2">
                        <input id="enhanced_privacy" type="checkbox"
                               class="mt-1 h-4 w-4 text-indigo-600 border-gray-300 rounded">
                        <div>
                            <label for="enhanced_privacy" class="text-sm font-medium text-gray-700">Enhanced Financial Privacy</label>
                            <p class="text-xs text-gray-500">Also redact balances, credit limit, rates (Barclaycard always applies this)</p>
                        </div>
                    </div>

                    <!-- Submit -->
                    <button id="submit-btn" onclick="processFiles()"
                            class="w-full flex justify-center items-center gap-2 py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed">
                        <span id="btn-text">Redact PDFs</span>
                        <svg id="btn-spinner" class="hidden animate-spin h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
                            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
                        </svg>
                    </button>
                </div>
            </div>
        </div>

        <!-- Results area — files appear here as they finish -->
        <div id="results" class="space-y-3"></div>

        <!-- Google Form -->
        <section class="bg-white p-8 rounded-lg shadow-md mt-8">
            <h2 class="text-2xl font-bold mb-6 text-center text-gray-800">Need a Custom Solution?</h2>
            <iframe src="https://docs.google.com/forms/d/e/1FAIpQLSd2PkHw7ATLfQYwL0CwdkKOnLynPU6mRweu5Zs5PCkKBeVB1g/viewform?usp=sf_link"
                    class="w-full h-[600px]" frameborder="0">Loading…</iframe>
        </section>
    </main>

    <footer class="w-full text-center py-4 bg-gray-200">
        <p class="text-gray-600">&copy; 2024 Credit Card Statement Redaction Tool. All rights reserved.</p>
    </footer>

<script>
// --- Provider UI ---
function updateProviderUI() {
    const provider = document.getElementById('provider').value;
    const tip = document.getElementById('barclaycard-tip');
    const kw  = document.getElementById('keywords');
    if (provider === 'barclaycard') {
        tip.classList.remove('hidden');
        kw.placeholder = 'e.g. Tfl Travel, Hyperoptic, Your-Saving';
    } else {
        tip.classList.add('hidden');
        kw.placeholder = 'e.g. Office Supplies, Travel, Client Dinner';
    }
}
document.getElementById('provider').addEventListener('change', updateProviderUI);
document.addEventListener('DOMContentLoaded', updateProviderUI);

// --- File picker ---
const pdfInput  = document.getElementById('pdf-input');
const dropZone  = document.getElementById('drop-zone');
const fileList  = document.getElementById('file-list');

pdfInput.addEventListener('change', renderFileList);

dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('border-indigo-500'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('border-indigo-500'));
dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('border-indigo-500');
    // Merge dropped files with existing selection
    const dt = new DataTransfer();
    [...(pdfInput.files || [])].forEach(f => dt.items.add(f));
    [...e.dataTransfer.files].filter(f => f.type === 'application/pdf').forEach(f => dt.items.add(f));
    pdfInput.files = dt.files;
    renderFileList();
});

function renderFileList() {
    const files = [...pdfInput.files];
    fileList.innerHTML = files.map((f, i) =>
        `<li class="flex items-center justify-between bg-gray-50 rounded px-3 py-1">
            <span class="truncate max-w-xs">📄 ${f.name}</span>
            <button onclick="removeFile(${i})" class="text-gray-400 hover:text-red-500 ml-2 text-xs">✕</button>
        </li>`
    ).join('');
    // Auto-detect Barclaycard if any file has "barclay" in name
    if (files.some(f => f.name.toLowerCase().includes('barclay'))) {
        document.getElementById('provider').value = 'barclaycard';
        updateProviderUI();
    }
}

function removeFile(idx) {
    const dt = new DataTransfer();
    [...pdfInput.files].forEach((f, i) => { if (i !== idx) dt.items.add(f); });
    pdfInput.files = dt.files;
    renderFileList();
}

// --- Process files one by one, show results as they finish ---
async function processFiles() {
    const files    = [...pdfInput.files];
    const keywords = document.getElementById('keywords').value.trim();
    const provider = document.getElementById('provider').value;
    const privacy  = document.getElementById('enhanced_privacy').checked;

    if (!files.length)  { alert('Please select at least one PDF.'); return; }
    if (!keywords)      { alert('Please enter at least one keyword.'); return; }

    const btn     = document.getElementById('submit-btn');
    const btnText = document.getElementById('btn-text');
    const spinner = document.getElementById('btn-spinner');
    btn.disabled  = true;
    spinner.classList.remove('hidden');
    btnText.textContent = `Processing 0 / ${files.length}…`;

    const results = document.getElementById('results');
    // Add a header if not already there
    if (!document.getElementById('results-heading')) {
        const h = document.createElement('h2');
        h.id = 'results-heading';
        h.className = 'text-xl font-bold text-gray-800 mb-2';
        h.textContent = 'Redacted Files';
        results.prepend(h);
    }

    let done = 0;
    // Process sequentially so server isn't overwhelmed
    for (const file of files) {
        const card = addPendingCard(file.name, results);
        try {
            const fd = new FormData();
            fd.append('pdf', file);
            fd.append('keywords', keywords);
            fd.append('provider', provider);
            if (privacy) fd.append('enhanced_privacy', 'on');

            const res  = await fetch('/redact', { method: 'POST', body: fd });
            const data = await res.json();

            if (data.error) {
                updateCard(card, 'error', file.name, null, data.error);
            } else {
                updateCard(card, 'success', data.filename, data.download_url,
                           `${data.kept_count} transaction${data.kept_count !== 1 ? 's' : ''} · £${data.total.toFixed(2)}`);
            }
        } catch (err) {
            updateCard(card, 'error', file.name, null, err.message);
        }
        done++;
        btnText.textContent = done < files.length ? `Processing ${done} / ${files.length}…` : 'Redact PDFs';
    }

    btn.disabled = false;
    spinner.classList.add('hidden');
    btnText.textContent = 'Redact PDFs';
}

function addPendingCard(filename, container) {
    const card = document.createElement('div');
    card.className = 'flex items-center gap-3 bg-white rounded-lg shadow-sm px-5 py-4 border border-gray-200';
    card.innerHTML = `
        <svg class="animate-spin h-5 w-5 text-indigo-500 flex-shrink-0" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
        </svg>
        <span class="text-gray-600 text-sm truncate flex-1">Processing <strong>${filename}</strong>…</span>`;
    container.appendChild(card);
    return card;
}

function updateCard(card, status, filename, url, detail) {
    if (status === 'success') {
        card.className = 'flex items-center gap-3 bg-green-50 rounded-lg shadow-sm px-5 py-4 border border-green-200';
        card.innerHTML = `
            <span class="text-green-500 text-xl flex-shrink-0">✅</span>
            <div class="flex-1 min-w-0">
                <p class="text-sm font-medium text-gray-800 truncate">${filename}</p>
                <p class="text-xs text-gray-500">${detail}</p>
            </div>
            <a href="${url}" download
               class="flex-shrink-0 flex items-center gap-1 text-sm font-medium text-indigo-600 hover:text-indigo-800 bg-indigo-50 hover:bg-indigo-100 px-3 py-1.5 rounded-md transition-colors">
                ⬇ Download
            </a>`;
    } else {
        card.className = 'flex items-center gap-3 bg-red-50 rounded-lg shadow-sm px-5 py-4 border border-red-200';
        card.innerHTML = `
            <span class="text-red-500 text-xl flex-shrink-0">❌</span>
            <div class="flex-1 min-w-0">
                <p class="text-sm font-medium text-gray-800 truncate">${filename}</p>
                <p class="text-xs text-red-600">${detail}</p>
            </div>`;
    }
}
</script>
</body>
</html>
'''

def process_single_file(file, keywords, provider, enhanced_privacy):
    """Process one uploaded PDF. Returns (redacted_path, total, kept_count)."""
    import uuid, fitz as _fitz

    tmp_in = f"tmp_in_{uuid.uuid4().hex}.pdf"
    file.save(tmp_in)
    try:
        base_name = os.path.splitext(file.filename)[0]
        tmp_out   = f"tmp_out_{uuid.uuid4().hex}.pdf"

        # Auto-detect provider from PDF content
        filename_lower = file.filename.lower()
        if provider == 'auto':
            try:
                _doc  = _fitz.open(tmp_in)
                _text = _doc[0].get_text().lower() if len(_doc) > 0 else ''
                _doc.close()
                if 'barclaycard' in _text or 'barclays' in _text or 'mastercard avios' in _text:
                    provider = 'barclaycard'
            except Exception:
                pass

        is_barclaycard = (
            provider == 'barclaycard' or
            'barclaycard' in filename_lower or
            'barclay' in filename_lower
        )

        if is_barclaycard:
            redacted_path, total, kept = redact_barclaycard(tmp_in, tmp_out, keywords)
            kept_count   = len(kept)
        elif enhanced_privacy:
            redacted_path, total = redact_amex_with_privacy(tmp_in, keywords, tmp_out, redact_financial=True)
            kept_count = -1  # AMEX doesn't return count
        else:
            redacted_path, total = redact_pdf_generic(tmp_in, keywords, tmp_out, provider)
            kept_count = -1

        # Rename with total in filename
        final_name = f"redacted_{base_name}_£{total:.2f}.pdf"
        if os.path.exists(redacted_path):
            os.rename(redacted_path, final_name)
            redacted_path = final_name

        return redacted_path, total, kept_count
    finally:
        if os.path.exists(tmp_in):
            os.remove(tmp_in)


@app.route('/', methods=['GET'])
def index():
    usage_count = update_usage_counter()
    providers   = get_all_providers()
    return render_template_string(HTML_TEMPLATE, usage_count=usage_count, providers=providers)


@app.route('/redact', methods=['POST'])
def redact_endpoint():
    """Process a single PDF and return JSON with download URL."""
    update_usage_counter()

    if 'pdf' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file     = request.files['pdf']
    keywords = [k.strip() for k in request.form.get('keywords', '').split(',') if k.strip()]
    provider = request.form.get('provider', 'auto')
    enhanced = request.form.get('enhanced_privacy') == 'on'

    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400
    if not keywords:
        return jsonify({'error': 'No keywords provided'}), 400

    try:
        redacted_path, total, kept_count = process_single_file(file, keywords, provider, enhanced)
        display_name = os.path.basename(redacted_path)
        return jsonify({
            'filename':     display_name,
            'download_url': f'/download/{redacted_path}',
            'total':        total,
            'kept_count':   kept_count,
        })
    except Exception as e:
        logger.error(f"Redact error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/download/<path:filename>')
def download_file(filename):
    @after_this_request
    def cleanup(response):
        try:
            os.remove(filename)
            logger.info(f"Deleted file: {filename}")
        except Exception as e:
            logger.error(f"Error deleting file {filename}: {str(e)}")
        return response

    return send_file(filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, port=5001)


@app.route('/debug-redact', methods=['POST'])
def debug_redact():
    """Debug endpoint — returns transaction list without redacting."""
    import uuid, fitz as _fitz
    from redact_barclaycard import group_transactions, COLUMNS
    
    file = request.files.get('pdf')
    if not file:
        return jsonify({'error': 'no file'}), 400
    
    tmp = f"tmp_dbg_{uuid.uuid4().hex}.pdf"
    file.save(tmp)
    try:
        doc = _fitz.open(tmp)
        result = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            txs, start_y, end_y = group_transactions(page)
            for tx in txs:
                result.append({
                    'page': page_num + 1,
                    'date': tx['date'],
                    'merchant': tx['merchant'],
                    'amount': tx['amount'],
                    'col': tx.get('col', 0),
                })
        doc.close()
        return jsonify({'count': len(result), 'transactions': result})
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@app.route('/debug-spans', methods=['POST'])
def debug_spans():
    """Return raw span x/y data for first 20 transaction-area spans."""
    import uuid, fitz as _fitz
    file = request.files.get('pdf')
    if not file: return jsonify({'error': 'no file'}), 400
    tmp = f"tmp_sp_{uuid.uuid4().hex}.pdf"
    file.save(tmp)
    try:
        doc = _fitz.open(tmp)
        page = doc[1]
        spans = []
        for block in page.get_text("dict")["blocks"]:
            if "lines" not in block: continue
            for line in block["lines"]:
                for span in line["spans"]:
                    t = span["text"].strip()
                    if t:
                        spans.append({"x": round(span["bbox"][0],1), "y": round(span["bbox"][1],1), "t": t[:40]})
        doc.close()
        # Return spans sorted by y, x — just the transaction area
        spans.sort(key=lambda s: (s["y"], s["x"]))
        return jsonify({"total_spans": len(spans), "spans": spans[:80]})
    finally:
        if os.path.exists(tmp): os.remove(tmp)
