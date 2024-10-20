from flask import Flask, request, send_file, after_this_request, render_template_string
import os
import logging
from redact_transactions import redact_transactions

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AMEX Credit Card Statement Redaction Tool | Whitelist Transactions</title>
    <meta name="description" content="Redact your AMEX credit card statements easily. Keep only the transactions you want by specifying keywords. Perfect for redacting reimbursements and financial privacy.">
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-100 min-h-screen flex flex-col">
    <header class="w-full bg-indigo-600 text-white text-center py-8">
        <h1 class="text-4xl font-bold">AMEX Statement Redaction Tool</h1>
        <p class="mt-2 text-xl">Whitelist Your Important Transactions</p>
    </header>
    <main class="flex-grow container mx-auto px-4 py-8">
        <div class="flex flex-col md:flex-row gap-8 mb-8">
            <section class="bg-white p-8 rounded-lg shadow-md md:w-1/2">
                <h2 class="text-2xl font-bold mb-4 text-gray-800">How It Works</h2>
                <p class="text-gray-600 mb-4">
                    This tool is designed specifically for AMEX credit card statements. It allows you to:
                </p>
                <ul class="list-disc list-inside text-gray-600 mb-4">
                    <li>Upload your AMEX statement PDF</li>
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
                        <label for="pdf" class="block text-sm font-medium text-gray-700">Select AMEX Statement PDF</label>
                        <input type="file" name="pdf" id="pdf" accept=".pdf" required
                               class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500">
                    </div>
                    <div>
                        <label for="keywords" class="block text-sm font-medium text-gray-700">Keywords to Keep (comma-separated)</label>
                        <input type="text" name="keywords" id="keywords" placeholder="e.g. Office Supplies, Travel, Client Dinner" required
                               class="mt-1 block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-indigo-500 focus:border-indigo-500">
                    </div>
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
                keywords = request.form.get('keywords', '').split(',')
                section_title = "Transaction Details"  # Assuming this is constant
                
                try:
                    output_filename = f"redacted_{file.filename}"
                    redacted_file_path, total_remaining = redact_transactions(input_path, section_title, keywords, output_filename)
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

    return render_template_string(HTML_TEMPLATE, message=message, error=error)

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
    app.run(debug=True)
