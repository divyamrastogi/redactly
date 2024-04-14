import fitz  # Import PyMuPDF
import re  # Import regex module

def redact_transactions(file_path, keep_keywords, currency_pattern, date_pattern, transaction_details):
    """
    Redacts transactions based on specified rules from the PDF file.

    Args:
    file_path (str): The path to the PDF file to be redacted.
    keep_keywords (list): A list of keywords to be kept in the document.
    currency_pattern (str): Regex pattern to identify currency amounts.
    date_pattern (str): Regex pattern to identify dates.
    transaction_details (str): Text indicating the start of transaction details.
    """
    doc = fitz.open(file_path)  # Open the document
    transaction_start = False  # Flag to indicate if we are in the transaction details section

    for page_num in range(len(doc)):  # Iterate through each page
        page = doc.load_page(page_num)
        text_instances = page.get_text("dict")["blocks"]  # Get text instances as a dictionary

        for inst in text_instances:
            if 'lines' in inst:  # Ensure we are looking at text
                for line in inst['lines']:
                    spans = line['spans']
                    for span in spans:
                        text = span['text']
                        if transaction_details in text:
                            # Start of transaction details section
                            transaction_start = True
                        elif transaction_start:
                            if not any(keyword.lower() in text.lower() for keyword in keep_keywords):
                                # Redact entire line if it's part of transaction details and doesn't contain keep_keywords
                                rect = fitz.Rect(span['bbox'])
                                page.add_redact_annot(rect, text=" ")
                            elif re.search(currency_pattern, text) and not re.search(date_pattern, text):
                                # Redact currency amounts not part of a date
                                rect = fitz.Rect(span['bbox'])
                                page.add_redact_annot(rect, text=" ")

        page.apply_redactions()  # Apply redactions for the current page

    redacted_file_path = "redacted_" + file_path
    doc.save(redacted_file_path)  # Save the redacted document
    doc.close()

    return redacted_file_path

# Example usage
file_path = "statement.pdf"
keep_keywords = ['TFL', 'Travel', 'Trainline']
currency_pattern = r'£\d+\.?\d*'  # Pattern to match currency amounts
date_pattern = r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\b'  # Pattern to match dates like "Mar 7"
transaction_details = "Transaction Details"  # Case sensitive text to detect the start of transaction details

redacted_file_path = redact_transactions(file_path, keep_keywords, currency_pattern, date_pattern, transaction_details)
print(f"Redacted PDF saved as: {redacted_file_path}")
