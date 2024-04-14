import fitz  # Import PyMuPDF

def redact_below_transaction_details(file_path, section_title, keep_keywords):
    """
    Redacts transactions below the 'Transaction Details' section that do not contain specified keywords.

    Args:
    file_path (str): The path to the PDF file to be redacted.
    section_title (str): The title indicating the start of the transaction details section.
    keep_keywords (list): A list of keywords to be kept in the document.
    """
    doc = fitz.open(file_path)  # Open the document
    for page_num in range(len(doc)):  # Iterate through each page
        page = doc.load_page(page_num)
        text_instances = page.get_text("dict")["blocks"]  # Get text instances as a dictionary
        transaction_start_y = None  # Initialize y-coordinate of the transaction details section

        # First, find the y-coordinate of the "Transaction Details" section
        for inst in text_instances:
            if 'lines' in inst:  # Ensure we are looking at text
                for line in inst['lines']:
                    for span in line['spans']:
                        if section_title in span['text']:
                            transaction_start_y = span['bbox'][3]  # Use the bottom y-coordinate
                            break
                    if transaction_start_y is not None:
                        break
            if transaction_start_y is not None:
                break

        # If the "Transaction Details" section is found, redact transactions below it
        if transaction_start_y is not None:
            for inst in text_instances:
                if 'lines' in inst and inst['bbox'][1] > transaction_start_y:  # Check if below the section
                    for line in inst['lines']:
                        for span in line['spans']:
                            text = span['text']
                            if not any(keyword.lower() in text.lower() for keyword in keep_keywords):
                                rect = fitz.Rect(span['bbox'])
                                page.add_redact_annot(rect, text=" ")

        page.apply_redactions()  # Apply redactions for the current page

    redacted_file_path = "redacted_" + file_path
    doc.save(redacted_file_path)  # Save the redacted document
    doc.close()

    return redacted_file_path

# Example usage
file_path = "statement.pdf"
section_title = "Transaction Details"
keep_keywords = ['TFL', 'Travel', 'Trainline']
redacted_file_path = redact_below_transaction_details(file_path, section_title, keep_keywords)
print(f"Redacted PDF saved as: {redacted_file_path}")
