from flask import Flask, request, send_file, after_this_request, render_template_string
import os
import logging
from redact_transactions import redact_transactions
from redact_generic import redact_pdf_generic
from redact_financial_details import redact_barclaycard_with_privacy, redact_amex_with_privacy
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
                    <div>
                        <label for="pdf" class="block text-sm font-medium text-gray-700">Select Credit Card Statement PDF</label>
                        <input type="file" name="pdf" id="pdf" accept=".pdf" required
                               class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500">
                    </div>
                    <div>
                        <label for="keywords" class="block text-sm font-medium text-gray-700">Keywords to Keep (comma-separated)</label>
                        <input type="text" name="keywords" id="keywords" placeholder="e.g. Office Supplies, Travel, Client Dinner" required
                               class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500">
                    </div>
                    <div id="privacy-option" class="hidden">
                        <div class="flex items-center">
                            <input type="checkbox" name="enhanced_privacy" id="enhanced_privacy" 
                                   class="h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-gray-300 rounded">
                            <label for="enhanced_privacy" class="ml-2 block text-sm text-gray-700">
                                <strong>Enhanced Financial Privacy</strong>
                            </label>
                        </div>
                        <p class="mt-1 text-xs text-gray-500">
                            Also redact personal details, account numbers, balances, and financial information while preserving statement structure
                        </p>
                    </div>
                    <script>
                        // Show/hide privacy option based on provider selection
                        document.getElementById('provider').addEventListener('change', function() {
                            const privacyOption = document.getElementById('privacy-option');
                            const selectedProvider = this.value;
                            // Always show privacy option for AMEX and Barclaycard
                            privacyOption.classList.remove('hidden');
                        });
                        
                        // Show on page load
                        document.addEventListener('DOMContentLoaded', function() {
                            document.getElementById('privacy-option').classList.remove('hidden');
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
                    output_filename = f"redacted_{file.filename}"
                    
                    # Check if enhanced privacy is requested
                    if enhanced_privacy:
                        if provider == 'barclaycard' or (provider == 'auto' and 'barclaycard' in file.filename.lower()):
                            # Use enhanced financial privacy redaction for Barclaycard
                            redacted_file_path, total_remaining = redact_barclaycard_with_privacy(
                                input_path, keywords, output_filename, redact_financial=True
                            )
                        else:
                            # Use enhanced financial privacy redaction for AMEX (default)
                            redacted_file_path, total_remaining = redact_amex_with_privacy(
                                input_path, keywords, output_filename, redact_financial=True
                            )
                    else:
                        # Use standard generic redaction
                        redacted_file_path, total_remaining = redact_pdf_generic(input_path, keywords, output_filename, provider)
                    
                    # Determine currency symbol based on provider/amount
                    currency_symbol = '£'  # Default to GBP for now
                    
                    # Build success message
                    privacy_note = ""
                    if enhanced_privacy and (provider == 'barclaycard' or provider == 'auto'):
                        privacy_note = "<br><span class='text-sm text-green-600'>✓ Enhanced Financial Privacy applied - credit limits, balances, and payment amounts redacted</span>"
                    
                    message = f'''
                    Total of remaining transactions: {currency_symbol}{total_remaining:.2f}{privacy_note}<br>
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
