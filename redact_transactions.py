import fitz  # PyMuPDF
import re

# Patterns
CURRENCY_PATTERN = r'£\d{1,3}(,\d{3})*(\.\d+)?'
DATE_PATTERN = r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\b'
DECIMAL_PATTERN = r'\d{1,3}(,\d{3})*(\.\d+)'

def get_transaction_details_y_coord(page, section_title):
    """Find the y-coordinate of the bottom of the "Transaction Details" section."""
    text_instances = page.get_text("dict")["blocks"]
    for inst in text_instances:
        if 'lines' in inst:
            for line in inst['lines']:
                for span in line['spans']:
                    if section_title in span['text']:
                        return span['bbox'][3]
    return None

def redact_transactions(file_path, section_title, keep_keywords, output_filename):
    """Redact transactions that do not contain specified keywords from the PDF file."""
    doc = fitz.open(file_path)

    for page_num in range(len(doc)):
        page = doc[page_num]
        text_instances = page.get_text("dict")["blocks"]

        decimals_to_redact = {}
        limits_to_redact = []

        transaction_start_y = get_transaction_details_y_coord(page, section_title)

        # First pass: Identify decimals and limits
        for inst in text_instances:
            if 'lines' in inst:
                for line in inst['lines']:
                    for span in line['spans']:
                        text, bbox = span['text'], span['bbox']
                        y_coord_int = int(bbox[1])
                        if re.search(DECIMAL_PATTERN, text):
                            decimals_to_redact[y_coord_int] = bbox
                        elif "Limit £" in text:
                            limits_to_redact.append(bbox)

        # Second pass: Process and redact
        for inst in text_instances:
            if 'lines' in inst:
                for line in inst['lines']:
                    for span in line['spans']:
                        text, bbox = span['text'], span['bbox']
                        rect = fitz.Rect(bbox)
                        y_coord_int = int(bbox[1])

                        if any(keyword in text for keyword in keep_keywords):
                            # Unmark whitelisted transactions
                            for y in range(y_coord_int - 1, y_coord_int + 2):
                                decimals_to_redact.pop(y, None)
                        elif re.search(CURRENCY_PATTERN, text):
                            # Redact currency amounts not in whitelist
                            page.add_redact_annot(rect, fill=(0, 0, 0))
                        elif any(rect.intersects(fitz.Rect(bbx)) and bbox != bbx for bbx in limits_to_redact):
                            # Redact limits
                            page.add_redact_annot(rect, fill=(0, 0, 0))
                        elif (transaction_start_y is not None and
                              y_coord_int > transaction_start_y and
                              not re.search(DATE_PATTERN, text) and
                              not re.search(DECIMAL_PATTERN, text)):
                            # Redact transaction descriptions
                            page.add_redact_annot(rect, fill=(0, 0, 0))

        # Redact remaining decimal amounts
        for bbox in decimals_to_redact.values():
            page.add_redact_annot(fitz.Rect(bbox), fill=(0, 0, 0))

        page.apply_redactions()

    doc.save(output_filename)
    doc.close()

# Example usage
input_pdf = 'temp_input.pdf'
output_pdf = 'output.pdf'
whitelist = ['TFL', 'Transport for London', 'Trainline']

redact_transactions(input_pdf, "Transaction Details", whitelist, output_pdf)