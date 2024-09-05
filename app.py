from flask import Flask, request, send_file
import os
import re
from redact import redact_transactions, get_transaction_details_y_coord

app = Flask(__name__)

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
                redacted_file_path = redact_transactions(input_path, section_title, keywords, file.filename)
                return send_file(redacted_file_path, as_attachment=True)
            finally:
                # Clean up temporary files
                if os.path.exists(input_path):
                    os.remove(input_path)
                if os.path.exists(redacted_file_path):
                    os.remove(redacted_file_path)

    return '''
    <form method="post" enctype="multipart/form-data">
      <input type="file" name="pdf">
      <input type="text" name="keywords" placeholder="Keywords (comma-separated)">
      <input type="submit" value="Redact">
    </form>
    '''

if __name__ == '__main__':
    app.run(debug=True)
