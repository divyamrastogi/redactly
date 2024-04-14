import fitz  # Import PyMuPDF
import re  # Import regex module

def redact_transactions(file_path, keep_keywords):
    """
    Redacts transactions that do not contain specified keywords from the PDF file.

    Args:
    file_path (str): The path to the PDF file to be redacted.
    keep_keywords (list): A list of keywords to be kept in the document.
    """
    doc = fitz.open(file_path)  # Open the document
    decimal_pattern = r'\d+\.\d+'  # Regex pattern to identify decimal numbers

    for page_num in range(len(doc)):  # Iterate through each page
        page = doc.load_page(page_num)
        text_instances = page.get_text("dict")["blocks"]  # Get text instances as a dictionary

        # Dictionary to store decimal amounts and their eligibility for redaction
        decimals_to_redact = {}

        # Identify all decimal amounts and mark them for redaction
        for inst in text_instances:
            if 'lines' in inst:  # Ensure we are looking at text
                for line in inst['lines']:
                    for span in line['spans']:
                        text = span['text']
                        bbox = span['bbox']
                        if re.search(decimal_pattern, text):
                            # Mark the decimal for redaction by its y-coordinate
                            decimals_to_redact[bbox[1]] = bbox

        # Whitelist transactions with keep_keywords
        for inst in text_instances:
            if 'lines' in inst:  # Ensure we are looking at text
                for line in inst['lines']:
                    for span in line['spans']:
                        text = span['text']
                        bbox = span['bbox']
                        if any(keyword in text for keyword in keep_keywords):
                            # If the line contains a keyword, unmark the associated decimal
                            if bbox[1] in decimals_to_redact:
                                decimals_to_redact.pop(bbox[1])

        # Redact the remaining decimal amounts
        for y_coord, bbox in decimals_to_redact.items():
            rect = fitz.Rect(bbox)
            page.add_redact_annot(rect, text=" ")

        page.apply_redactions()  # Apply redactions for the current page

    redacted_file_path = "redacted_" + file_path
    doc.save(redacted_file_path)  # Save the redacted document
    doc.close()

    return redacted_file_path

# Example usage
file_path = "statement.pdf"  # Update with the correct file path
keep_keywords = ['TFL', 'Travel', 'Trainline']
redacted_file_path = redact_transactions(file_path, keep_keywords)
print(f"Redacted PDF saved as: {redacted_file_path}")
