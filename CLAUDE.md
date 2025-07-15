# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Flask web application for redacting American Express credit card statements. Users upload a PDF and specify keywords - transactions matching those keywords are kept while all others are redacted.

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
python app.py
# Server runs on http://127.0.0.1:5000

# Run in production mode (as configured for Heroku)
gunicorn app:app
```

## Architecture Overview

The application uses a simple monolithic architecture:

- **app.py**: Flask web server handling routes, file uploads, and serving the HTML interface
  - Routes: `/` (main page), `/download/<filename>` (processed PDF download)
  - Tracks usage in `usage_counter.txt`
  - Inline HTML template using Tailwind CSS
  
- **redact_transactions.py**: Core redaction logic
  - `redact_pdf_with_whitelist()`: Main function that processes PDFs
  - Pattern matching for dates, amounts, and transaction descriptions
  - Calculates total of remaining transactions
  - Returns both the redacted PDF and the total amount

- **redact.py**: Alternative/older redaction implementation (appears to be superseded)

## Key Implementation Details

1. **PDF Processing**: Uses PyMuPDF (fitz) for all PDF manipulation
2. **Redaction Logic**: Whitelist-based - keeps only transactions containing specified keywords
3. **Pattern Recognition**: 
   - Date patterns: `r"\d{1,2}/\d{1,2}"`
   - Amount patterns: `r"\$[\d,]+\.\d{2}"`, `r"\d+\.\d{2}"`
4. **File Handling**: Temporary files are created in the system temp directory and cleaned up after download
5. **Frontend**: Single-page application with inline HTML/CSS, no separate frontend build process

## Important Notes

- The application is specifically designed for AMEX credit card statements
- No authentication or user management - it's a public tool
- No database - only file-based storage for the usage counter
- Google Analytics is integrated for usage tracking
- The `node_modules` directory exists but appears unused (no package.json)