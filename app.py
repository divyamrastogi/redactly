from flask import Flask, request, send_file, after_this_request
import os
import logging
from redact_transactions import redact_transactions

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'pdf' not in request.files:
            return 'No file part'
        file = request.files['pdf']
        if file.filename == '':
            return 'No selected file'
        if file:
            input_path = 'temp_input.pdf'
            file.save(input_path)
            keywords = request.form.get('keywords', '').split(',')
            section_title = "Transaction Details"  # Assuming this is constant
            
            try:
                output_filename = f"redacted_{file.filename}"
                redacted_file_path, total_remaining = redact_transactions(input_path, section_title, keywords, output_filename)
                return f'''
                Total of remaining transactions: £{total_remaining:.2f}<br>
                <a href="/download/{redacted_file_path}">Download Redacted PDF</a>
                '''
            except Exception as e:
                logger.error(f"An error occurred: {str(e)}", exc_info=True)
                return f"An error occurred: {str(e)}", 500
            finally:
                # Clean up temporary files
                if os.path.exists(input_path):
                    os.remove(input_path)

    return '''
    <form method="post" enctype="multipart/form-data">
      <input type="file" name="pdf">
      <input type="text" name="keywords" placeholder="Keywords (comma-separated)">
      <input type="submit" value="Redact">
    </form>
    '''

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
