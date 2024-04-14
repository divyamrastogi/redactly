import fitz  # Import PyMuPDF
import re  # Import regex module

def redact_unmatched_transactions(file_path, keep_keywords):
    """
    Redacts decimal numbers if they are on the same x-coordinate as text not including specified keywords.

    Args:
    file_path (str): The path to the PDF file to be redacted.
    keep_keywords (list): A list of keywords to be kept in the document.
    """
    doc = fitz.open(file_path)  # Open the document
    decimal_pattern = r'\d+\.\d+'  # Regex pattern to identify decimal numbers

    for page_num in range(len(doc)):  # Iterate through each page
        page = doc.load_page(page_num)
        text_instances = page.get_text("dict")["blocks"]  # Get text instances as a dictionary

        # Lists to store coordinates of keywords and decimal numbers
        keyword_coords = []
        decimal_coords = []

        # First, identify and store coordinates of keywords and decimal numbers
        for inst in text_instances:
            if 'lines' in inst:  # Ensure we are looking at text
                for line in inst['lines']:
                    for span in line['spans']:
                        text = span['text']
                        if any(keyword.lower() in text.lower() for keyword in keep_keywords):
                            keyword_coords.append(span['bbox'])
                        elif re.search(decimal_pattern, text):
                            decimal_coords.append((span['bbox'], text))  # Store text for debugging

        # Check each decimal number's x-coordinate against keyword x-coordinates
        for decimal_rect, decimal_text in decimal_coords:
            match_found = False
            for keyword_rect in keyword_coords:
                # Check if the decimal number is on the same x-coordinate as a keyword (within a tolerance)
                if abs(decimal_rect[0] - keyword_rect[0]) < 5:  # Tolerance of 5 units
                    match_found = True
                    break

            if not match_found:
                # Redact the decimal number
                rect = fitz.Rect(decimal_rect)
                page.add_redact_annot(rect, text=" ")

        page.apply_redactions()  # Apply redactions for the current page

    redacted_file_path = "redacted_" + file_path
    doc.save(redacted_file_path)  # Save the redacted document
    doc.close()

    return redacted_file_path

# Example usage
file_path = "statement.pdf"
keep_keywords = ['TFL', 'Travel', 'Trainline']
redacted_file_path = redact_unmatched_transactions(file_path, keep_keywords)
print(f"Redacted PDF saved as: {redacted_file_path}")
