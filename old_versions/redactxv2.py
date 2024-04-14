import fitz  # Import PyMuPDF
import re  # Import regex module

def redact_transactions_with_keywords(file_path, keep_keywords):
    """
    Redacts decimal numbers if they are not associated with specified keywords based on x-coordinates.

    Args:
    file_path (str): The path to the PDF file to be redacted.
    keep_keywords (dict): A dictionary of keywords to be kept in the document with initial value False.
    """
    doc = fitz.open(file_path)  # Open the document
    decimal_pattern = r'\d+\.\d+'  # Regex pattern to identify decimal numbers

    for page_num in range(len(doc)):  # Iterate through each page
        page = doc.load_page(page_num)
        text_instances = page.get_text("dict")["blocks"]  # Get text instances as a dictionary

        # Initialize a dictionary to track decimal numbers and their eligibility for redaction
        decimal_coords = {}

        # Identify and store coordinates of decimal numbers and check against keywords
        for inst in text_instances:
            if 'lines' in inst:  # Ensure we are looking at text
                for line in inst['lines']:
                    for span in line['spans']:
                        text = span['text']
                        bbox = span['bbox']
                        x_coord = bbox[0]  # Left x-coordinate of the bounding box

                        # Check if the text is a decimal number
                        if re.search(decimal_pattern, text):
                            decimal_coords[x_coord] = (bbox, text, True)  # Assume eligible for redaction initially

                        # Check if the text contains any of the keep keywords
                        for keyword in keep_keywords:
                            if keyword.lower() in text.lower():
                                keep_keywords[keyword] = True  # Mark keyword as found
                                if x_coord in decimal_coords:
                                    # If a decimal number shares the x-coordinate, mark it as not eligible for redaction
                                    decimal_coords[x_coord] = (decimal_coords[x_coord][0], decimal_coords[x_coord][1], False)

        # Redact decimal numbers not associated with keywords
        for x_coord, (bbox, text, redact) in decimal_coords.items():
            if redact:
                rect = fitz.Rect(bbox)
                page.add_redact_annot(rect, text=" ")

        page.apply_redactions()  # Apply redactions for the current page

    redacted_file_path = "redacted_" + file_path
    doc.save(redacted_file_path)  # Save the redacted document
    doc.close()

    return redacted_file_path

# Example usage
file_path = "statement.pdf"
keep_keywords = {'TFL': False, 'Travel': False, 'Trainline': False}
redacted_file_path = redact_transactions_with_keywords(file_path, keep_keywords)
print(f"Redacted PDF saved as: {redacted_file_path}")
