from flask import Flask, request, send_file
import fitz  # Import PyMuPDF
import re  # Import regex module
import os

from redact import redact_transactions

app = Flask(__name__)

# Include your existing functions here: get_transaction_details_y_coord and redact_transactions

@app.route('/')
def index():
    return '''
    <form method="post" action="/redact" enctype="multipart/form-data">
      PDF File: <input type="file" name="pdf"><br>
      Keywords (comma-separated): <input type="text" name="keywords"><br>
      <input type="submit" value="Redact">
    </form>
    '''

@app.route('/redact', methods=['POST'])
def redact():
    pdf = request.files['pdf']
    keywords = request.form['keywords'].split(',')
    section_title = "Transaction Details"  # Assuming this is constant for simplicity

    # Save the uploaded file temporarily
    temp_pdf_path = "temp_input.pdf"
    pdf.save(temp_pdf_path)

    # Redact the PDF
    redacted_pdf_path = redact_transactions(temp_pdf_path, section_title, keywords, pdf.filename)

    # Send the redacted PDF file to the user
    return send_file(redacted_pdf_path, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
