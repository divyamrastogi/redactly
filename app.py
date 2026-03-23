from flask import Flask, request, send_file, after_this_request, render_template_string
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
    <meta name="description" content="Redact your credit card statements easily. Supports AMEX, Barclaycard, Visa, Mastercard and more. Keep only the transactions you want by specifying keywords. Perfect for expense reimbursements and financial privacy.">
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    
    <!-- Google tag (gtag.js) -->
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
        {% if usage_count %}
        <p class="mt-2 text-sm">This tool has been used {{ usage_count }} times</p>
        {% endif %}
    </header>
    <main class="flex-grow container mx-auto px-4 py-8">
        <div class="flex flex-col md:flex-row gap-8 mb-8">
            <section class="bg-white p-8 rounded-lg shadow-md md:w-1/2">
                <h2 class="text-2xl font-bold mb-4 text-gray-800">How It Works</h2>
                <p class="text-gray-600 mb-4">
                    This tool works with American Express and Barclaycard credit card statements. It allows you to:
                </p>
                <ul class="list-disc list-inside text-gray-600 mb-4">
                    <li>Upload your credit card statement PDF</li>
                    <li>Auto-detect provider or manually select</li>
                    <li>Specify keywords for transactions you want to keep</li>
                    <li>Automatically redact all other transactions</li>
                </ul>
                <p class="text-gray-600 mb-4">
                    <strong>Example:</strong> If you want to keep only work-related expenses, you might use keywords like "Office Supplies", "Travel", or "Client Dinner".
                </p>
                <p class="text-gray-600">
                    Perfect for submitting reimbursements by maintaining financial privacy, or focusing on specific types of transactions.
                </p>
            </section>
            <div class="bg-white p-8 rounded-lg shadow-md md:w-1/2">
                <h2 class="text-2xl font-bold mb-6 text-center text-gray-800">Redact Your Statement</h2>
                {% if message %}
                    <div class="mb-4 p-4 rounded {% if error %}bg-red-100 text-red-700{% else %}bg-green-100 text-green-700{% endif %}">
                        {{ message | safe }}
                    </div>
                {% endif %}
                <form method="post" enctype="multipart/form-data" class="space-y-4">
                    <div>
                        <label for="provider" class="block text-sm font-medium text-gray-700">Credit Card Provider</label>
                        <select name="provider" id="provider" 
                                class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500">
                            {% for value, display in providers %}
                            <option value="{{ value }}" {% if value == 'auto' %}selected{% endif %}>{{ display }}</option>
                            {% endfor %}
                        </select>
                        <p class="mt-1 text-xs text-gray-500">Leave as Auto-detect for automatic provider detection</p>
                    </div>

                    <!-- Barclaycard tip — shown when barclaycard is selected -->
                    <div id="barclaycard-tip" class="hidden bg-blue-50 border border-blue-200 rounded-md p-3 text-sm text-blue-700">
                        <strong>🏦 Barclaycard detected</strong> — uses precise row-by-row redaction.
                        Enter merchant name keywords (e.g. <em>Hyperoptic, Tfl Travel, Your-Saving</em>).
                    </div>

                    <div>
                        <label for="pdf" class="block text-sm font-medium text-gray-700">Select Credit Card Statement PDF</label>
                        <input type="file" name="pdf" id="pdf" accept=".pdf" required
                               class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500">
                    </div>
                    <div>
                        <label for="keywords" class="block text-sm font-medium text-gray-700">Keywords to Keep (comma-separated)</label>
                        <input type="text" name="keywords" id="keywords" placeholder="e.g. Hyperoptic, Tfl Travel, Your-Saving" required
                               class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500">
                    </div>
                    <div id="privacy-option">
                        <div class="flex items-center">
                            <input type="checkbox" name="enhanced_privacy" id="enhanced_privacy" 
                                   class="h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded">
                            <label for="enhanced_privacy" class="ml-2 block text-sm text-gray-700">
                                <strong>Enhanced Financial Privacy</strong>
                            </label>
                        </div>
                        <p class="mt-1 text-xs text-gray-500">
                            Also redact personal details, account numbers, and balances (AMEX only — Barclaycard uses precise redaction regardless)
                        </p>
                    </div>
                    <script>
                        function updateProviderUI() {
                            const provider = document.getElementById('provider').value;
                            const tip = document.getElementById('barclaycard-tip');
                            const keywords = document.getElementById('keywords');
                            if (provider === 'barclaycard') {
                                tip.classList.remove('hidden');
                                keywords.placeholder = 'e.g. Hyperoptic, Tfl Travel, Your-Saving';
                            } else {
                                tip.classList.add('hidden');
                                keywords.placeholder = 'e.g. Office Supplies, Travel, Client Dinner';
                            }
                        }
                        document.getElementById('provider').addEventListener('change', updateProviderUI);
                        document.addEventListener('DOMContentLoaded', updateProviderUI);

                        // Auto-detect Barclaycard from filename
                        document.getElementById('pdf').addEventListener('change', function() {
                            const filename = this.files[0]?.name?.toLowerCase() || '';
                            if (filename.includes('barclay')) {
                                document.getElementById('provider').value = 'barclaycard';
                                updateProviderUI();
                            }
                        });
                    </script>
                    <button type="submit" class="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500">
                        Redact PDF
                    </button>
                </form>
            </div>
        </div>
        
        <!-- Google Form iframe section -->
        <section class="bg-white p-8 rounded-lg shadow-md mt-8">
            <h2 class="text-2xl font-bold mb-6 text-center text-gray-800">Need a Custom Solution?</h2>
            <p class="text-gray-600 mb-4 text-center">
                If you need a modified version of this tool for your specific needs, please fill out the form below:
            </p>
            <div class="aspect-w-16 aspect-h-9">
                <iframe src="https://docs.google.com/forms/d/e/1FAIpQLSd2PkHw7ATLfQYwL0CwdkKOnLynPU6mRweu5Zs5PCkKBeVB1g/viewform?usp=sf_link" 
                        class="w-full h-[600px]" frameborder="0" marginheight="0" marginwidth="0">
                    Loading…
                </iframe>
            </div>
        </section>
    </main>
    <footer class="w-full text-center py-4 bg-gray-200">
        <p class="text-gray-600">&copy; 2024 AMEX Statement Redaction Tool. All rights reserved.</p>
    </footer>
</body>
</html>
'''

@app.route('/', methods=['GET', 'POST'])
def index():
    message = None
    error = False
    usage_count = update_usage_counter()
    
    # Get providers for dropdown
    providers = get_all_providers()

    if request.method == 'POST':
        if 'pdf' not in request.files:
            message = 'No file part'
            error = True
        else:
            file = request.files['pdf']
            if file.filename == '':
                message = 'No selected file'
                error = True
            elif file:
                input_path = 'temp_input.pdf'
                file.save(input_path)
                keywords = [k.strip() for k in request.form.get('keywords', '').split(',') if k.strip()]
                provider = request.form.get('provider', 'auto')
                enhanced_privacy = request.form.get('enhanced_privacy') == 'on'
                
                try:
                    base_name = os.path.splitext(file.filename)[0]
                    output_filename = f"redacted_{base_name}.pdf"  # total appended after processing

                    # Auto-detect Barclaycard from filename, explicit selection, or PDF content
                    filename_lower = file.filename.lower()
                    if provider == 'auto':
                        # Peek at PDF text to detect provider reliably
                        try:
                            import fitz as _fitz
                            _doc = _fitz.open(input_path)
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
                        # Use the new precise Barclaycard redaction script
                        redacted_file_path, total_remaining, kept = redact_barclaycard(
                            input_path, output_filename, keywords
                        )
                        # Rename output file to include total
                        total_filename = f"redacted_{base_name}_£{total_remaining:.2f}.pdf"
                        if os.path.exists(redacted_file_path):
                            os.rename(redacted_file_path, total_filename)
                            redacted_file_path = total_filename
                        kept_count = len(kept)
                        message = f'''
                        <strong>✅ Barclaycard statement redacted</strong><br>
                        Kept {kept_count} transaction{"s" if kept_count != 1 else ""} totalling
                        <strong>£{total_remaining:.2f}</strong><br>
                        <a href="/download/{redacted_file_path}" class="text-indigo-600 hover:text-indigo-800 font-medium">
                            ⬇ Download Redacted PDF
                        </a>
                        '''
                    elif enhanced_privacy:
                        if provider == 'auto':
                            redacted_file_path, total_remaining = redact_amex_with_privacy(
                                input_path, keywords, output_filename, redact_financial=True
                            )
                        else:
                            redacted_file_path, total_remaining = redact_amex_with_privacy(
                                input_path, keywords, output_filename, redact_financial=True
                            )
                        message = f'''
                        Total of remaining transactions: £{total_remaining:.2f}
                        <br><span class='text-sm text-green-600'>✓ Enhanced Financial Privacy applied</span><br>
                        <a href="/download/{redacted_file_path}" class="text-indigo-600 hover:text-indigo-800">Download Redacted PDF</a>
                        '''
                    else:
                        redacted_file_path, total_remaining = redact_pdf_generic(input_path, keywords, output_filename, provider)
                        message = f'''
                        Total of remaining transactions: £{total_remaining:.2f}<br>
                        <a href="/download/{redacted_file_path}" class="text-indigo-600 hover:text-indigo-800">Download Redacted PDF</a>
                        '''
                except Exception as e:
                    logger.error(f"An error occurred: {str(e)}", exc_info=True)
                    message = f"An error occurred: {str(e)}"
                    error = True
                finally:
                    # Clean up temporary files
                    if os.path.exists(input_path):
                        os.remove(input_path)

    return render_template_string(HTML_TEMPLATE, message=message, error=error, usage_count=usage_count, providers=providers)

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
