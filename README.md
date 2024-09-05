# Simple PDF Redactor

This is a basic Flask application that redacts PDFs based on user-provided keywords.

## Setup

1. Install the required packages:
   ```
   pip install -r requirements.txt
   ```

2. Run the application:
   ```
   python app.py
   ```

3. Open a web browser and go to `http://127.0.0.1:5000/`

4. Upload a PDF file, enter keywords to redact (comma-separated), and click "Redact".

5. The redacted PDF will be downloaded automatically.

Note: This is a basic implementation and should be enhanced for production use.