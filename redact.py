import fitz  # Import PyMuPDF
import re  # Import regex module

#r'£\d{1,3}(,\d{3})*(\.\d+)?'
currency_pattern = r'£\d{1,3}(,\d{3})*(\.\d+)?'  # Pattern to match currency amounts
date_pattern = r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\b'  # Pattern to match dates like "Mar 7"

def get_transaction_details_y_coord(page, section_title):
    """
    Finds the y-coordinate of the bottom of the "Transaction Details" section.

    Args:
    page (fitz.Page): The page object to search in.
    section_title (str): The title indicating the start of the transaction details section.

    Returns:
    float: The y-coordinate of the bottom of the "Transaction Details" section, or None if not found.
    """
    text_instances = page.get_text("dict")["blocks"]
    for inst in text_instances:
        if 'lines' in inst:
            for line in inst['lines']:
                for span in line['spans']:
                    if section_title in span['text']:
                        # Return the bottom y-coordinate of the "Transaction Details" text
                        return span['bbox'][3]
    return None

def redact_transactions(file_path, section_title, keep_keywords, filename):
    """
    Redacts transactions that do not contain specified keywords from the PDF file,
    based on the integer y-coordinate.

    Args:
    file_path (str): The path to the PDF file to be redacted.
    keep_keywords (list): A list of keywords to be kept in the document.
    """
    doc = fitz.open(file_path)  # Open the document
    decimal_pattern = r'\d{1,3}(,\d{3})*(\.\d+)'  # Regex pattern to identify decimal numbers

    for page_num in range(len(doc)):  # Iterate through each page
        page = doc.load_page(page_num)
        text_instances = page.get_text("dict")["blocks"]  # Get text instances as a dictionary

        # Dictionary to store decimal amounts and their eligibility for redaction
        decimals_to_redact = {}
        limits_to_redact= []

        transaction_start_y = get_transaction_details_y_coord(page, section_title)

        # Identify all decimal amounts and mark them for redaction
        for inst in text_instances:
            if 'lines' in inst:  # Ensure we are looking at text
                for line in inst['lines']:
                    for span in line['spans']:
                        text = span['text']
                        bbox = span['bbox']
                        y_coord_int = int(bbox[1])  # Convert y-coordinate to integer
                        if re.search(decimal_pattern, text):
                            # Mark the decimal for redaction by its integer y-coordinate
                            decimals_to_redact[y_coord_int] = bbox
                        elif "Limit £" in text:
                            limits_to_redact.append(bbox)
                            # print(text, bbox)

        # Whitelist transactions with keep_keywords
        for inst in text_instances:
            if 'lines' in inst:  # Ensure we are looking at text
                for line in inst['lines']:
                    for span in line['spans']:
                        text = span['text']
                        bbox = span['bbox']
                        rect = fitz.Rect(bbox)
                        y_coord_int = int(bbox[1])  # Convert y-coordinate to integer
                        if any(keyword in text for keyword in keep_keywords):
                            # If the line contains a keyword, unmark the associated decimal
                            if y_coord_int in decimals_to_redact:
                                decimals_to_redact.pop(y_coord_int)
                            # Sometimes y coordinates for transactions do not exactly match, so we take a delta of 1 unit above and below
                            if y_coord_int-1 in decimals_to_redact:
                                decimals_to_redact.pop(y_coord_int-1)
                            if y_coord_int+1 in decimals_to_redact:
                                decimals_to_redact.pop(y_coord_int+1)
                        elif re.search(currency_pattern, text):
                            # Redact numbers which are not our whitelisted transactions that start with currency, i.e. probably some limits
                            page.add_redact_annot(rect, text=" ")
                        elif any(rect.intersects(fitz.Rect(bbx)) and bbox != bbx for bbx in limits_to_redact):
                            page.add_redact_annot(rect, text=" ")
                        else:
                            if transaction_start_y is not None:
                                # Search and redact transaction decription content below "Transaction Details 
                                # that is not integer i.e. our whitelisted transactions"
                                if not re.search(date_pattern, text) and not re.search(decimal_pattern, text):
                                    if y_coord_int > transaction_start_y:
                                        page.add_redact_annot(rect, text=" ")

        # Redact the remaining decimal amounts
        for y_coord, bbox in decimals_to_redact.items():
            rect = fitz.Rect(bbox)
            page.add_redact_annot(rect, text=" ")

        page.apply_redactions()  # Apply redactions for the current page

    redacted_file_path = "redacted_" + filename
    doc.save(redacted_file_path)  # Save the redacted document
    doc.close()

    return redacted_file_path

input_pdf = 'temp_input.pdf'
output_pdf = 'output.pdf'
whitelist = ['TFL', 'Transport for London', 'Trainline']

redact_transactions(input_pdf, "Transaction Details", whitelist, output_pdf)
