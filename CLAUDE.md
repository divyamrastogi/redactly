# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Flask web application for redacting credit card statements from American Express and Barclaycard. Users upload a PDF, select their credit card provider (or use auto-detection), and specify keywords - transactions matching those keywords are kept while all others are redacted.

**Supported Providers:**
- American Express
- Barclaycard

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

The application uses a modular architecture with provider-specific configurations:

- **app.py**: Flask web server handling routes, file uploads, and serving the HTML interface
  - Routes: `/` (main page), `/download/<filename>` (processed PDF download)
  - Tracks successfully redacted statements in `usage_counter.txt` (homepage views are read-only and never increment it)
  - Inline HTML template using Tailwind CSS with provider selection dropdown
  
- **redact_generic.py**: Main redaction logic (NEW)
  - `redact_pdf_generic()`: Primary function supporting multiple providers
  - Auto-detection and manual provider selection
  - Multi-line and single-line transaction parsing
  - Configurable patterns and section headers

- **provider_config.py**: Provider configuration system (NEW)
  - `ProviderConfig`: Data class for provider-specific settings
  - `PROVIDER_CONFIGS`: Dictionary of all supported providers
  - `detect_provider()`: Auto-detection based on PDF content
  - Flexible date, currency, and section header patterns

- **redact_transactions.py**: Legacy AMEX-specific redaction (LEGACY)
  - Original implementation for AMEX statements only
  - Still available for backward compatibility

- **redact.py**: Older implementation (appears to be superseded)

## Key Implementation Details

1. **PDF Processing**: Uses PyMuPDF (fitz) for all PDF manipulation
2. **Redaction Logic**: Whitelist-based - keeps only transactions containing specified keywords
3. **Provider Detection**: 
   - Auto-detection based on PDF content keywords
   - Manual selection via dropdown
   - Fallback to generic patterns if provider unknown
4. **Pattern Recognition**: 
   - **Flexible date patterns**: MMM DD, DD MMM, DD/MM/YYYY, YYYY-MM-DD, etc.
   - **Multi-currency support**: £, $, €, ¥, ₹ with amounts
   - **Section headers**: Provider-specific transaction section detection
5. **Transaction Parsing**:
   - **Span-by-span approach**: Simple and reliable processing of each text element
   - **Same-line keyword detection**: Checks for keywords within the same Y-coordinate
   - **Amount extraction**: Properly handles currency patterns and totals
6. **Enhanced Privacy Features**:
   - **Barclaycard**: Redacts personal details, credit limits, balances while preserving name and statement month
   - **AMEX**: Redacts customer name, address, account numbers, rewards info while preserving statement structure
7. **File Handling**: Temporary files are created in the system temp directory and cleaned up after download
8. **Frontend**: Single-page application with inline HTML/CSS, provider selection dropdown with privacy options

## Important Notes

- **Dual-provider support**: Works with AMEX and Barclaycard statements only
- **Enhanced Privacy Options**: Both providers support additional financial privacy redaction
- **Auto-detection**: Automatically identifies provider from PDF content
- **Span-by-span Processing**: Simple, reliable approach for transaction processing
- No authentication or user management - it's a public tool
- No database - only file-based storage for the usage counter
- Google Analytics is integrated for usage tracking
- The `node_modules` directory exists but appears unused (no package.json)

## Provider Configuration

The application is specifically configured for:
- **American Express**: MMM DD date format, multi-line transactions, £ currency
- **Barclaycard**: DD MMM date format, multi-line transactions, £ currency

Both providers have enhanced privacy redaction support that removes personal and financial details while preserving whitelisted transactions.