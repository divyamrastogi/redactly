"""
Enhanced redaction for credit card statements with financial privacy.
Supports Barclaycard and American Express statements.
"""
import fitz
import re
import os
import logging
from redact_generic import redact_pdf_generic

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def redact_financial_details_barclaycard(page):
    """
    Redact private financial details from a Barclaycard page while preserving name and statement month.
    
    Returns:
        int: Number of redactions applied
    """
    redaction_count = 0
    
    # Get all text with positioning
    text_dict = page.get_text("dict")
    
    # Patterns for financial information to redact (context-specific amounts)
    financial_redact_patterns = [
        # Credit card number (partial or full)
        r'\d{4}\s+\d{4}\s+\d{4}\s+\d{4}',
        r'Number\s+\d{4}\s+\d{4}\s+\d{4}\s+\d{4}',
        
        # Note: Address redaction removed - keeping addresses visible per user request
        
        # Specific dates (keep month/year, redact specific dates)
        r'\d{2}\s+July\s+2025',  # Specific day
        r'29\s+July\s+2025',    # Payment due date
        
        # Financial summary lines with context (including amounts)
        r'Your\s+new\s+balance:\s*£[\d,]+\.\d{2}',
        r'Your\s+previous\s+balance:\s*£[\d,]+\.\d{2}',
        r'Minimum\s+payment:\s*£[\d,]+\.\d{2}',
        r'Available\s+to\s+spend:\s*£[\d,]+\.\d{2}',
        r'Your\s+current\s+credit\s+limit:\s*£[\d,]+\.\d{2}',
        r'Estimated\s+interest\s+next\s+month:\s*£[\d,]+\.\d{2}',
        r'Payments\s+towards\s+your\s+account:\s*£[\d,]+\.\d{2}',
        r'Interest\s+charged:\s*£[\d,]+\.\d{2}',
        r'Transactions,\s+interest\s+and\s+charges',
        r'How\s+you\'ve\s+used\s+your\s+card',
        
        # Individual financial summary labels (will catch amounts nearby)
        r'Your\s+new\s+balance:',
        r'Your\s+previous\s+balance:',
        r'Minimum\s+payment:',
        r'Available\s+to\s+spend:',
        r'Your\s+current\s+credit\s+limit:',
        r'Estimated\s+interest\s+next\s+month:',
        r'Payments\s+towards\s+your\s+account:',
        r'Interest\s+charged:',
    ]
    
    # Context-specific amount patterns (only redact these in financial summary areas)
    summary_amount_patterns = [
        r'£[\d,]+\.\d{2}',  # Will be applied contextually
    ]
    
    # Patterns to preserve (never redact these)
    preserve_patterns = [
        r'Mr\s+D\s+M\s+Rastogi',  # Customer name
        r'July\s+2025',           # Statement month/year (without day)
        r'Mastercard',            # Card type
        r'barclaycard\.co\.uk',   # Website
        r'0800\s+\d+\s+\d+',      # Phone numbers
        r'0333\s+\d+\s+\d+',      # Phone numbers
    ]
    
    # Get page dimensions to identify financial summary areas vs transaction areas
    page_rect = page.rect
    page_height = page_rect.height
    
    # Define areas where amounts should be redacted (top ~40% of page for financial summaries)
    financial_summary_area_max_y = page_height * 0.4
    
    # Process each block
    for block in text_dict["blocks"]:
        if "lines" in block:
            for line in block["lines"]:
                for span in line["spans"]:
                    span_text = span["text"]
                    span_rect = fitz.Rect(span["bbox"])
                    
                    # Skip empty spans
                    if not span_text.strip():
                        continue
                    
                    # Check if this span should be preserved
                    should_preserve = False
                    for preserve_pattern in preserve_patterns:
                        if re.search(preserve_pattern, span_text, re.IGNORECASE):
                            should_preserve = True
                            logger.info(f"Preserving: '{span_text}' (matches preserve pattern)")
                            break
                    
                    if should_preserve:
                        continue
                    
                    # Check if this span contains financial information to redact
                    should_redact = False
                    matched_pattern = None
                    
                    for pattern in financial_redact_patterns:
                        if re.search(pattern, span_text):
                            should_redact = True
                            matched_pattern = pattern
                            break
                    
                    # Special handling for amounts - only redact in financial summary areas
                    if re.search(r'£[\d,]+\.\d{2}', span_text):
                        # Check if we're in a financial summary area (top of page) 
                        if span_rect.y0 <= financial_summary_area_max_y:
                            # Only redact standalone amounts in summary area
                            if re.match(r'^\s*£[\d,]+\.\d{2}\s*$', span_text):
                                should_redact = True
                                matched_pattern = "standalone amount in summary area"
                        # Skip amount redaction in transaction areas (lower part of page)
                    
                    if should_redact:
                        rect = fitz.Rect(span["bbox"])
                        page.add_redact_annot(rect, fill=(0, 0, 0))
                        redaction_count += 1
                        logger.info(f"Redacting financial detail: '{span_text}' (pattern: {matched_pattern})")
    
    return redaction_count

def redact_financial_details_amex(page):
    """
    Redact private financial details from an AMEX page while preserving statement structure.
    
    Returns:
        int: Number of redactions applied
    """
    redaction_count = 0
    
    # Get all text with positioning
    text_dict = page.get_text("dict")
    
    # Patterns for AMEX financial information to redact
    amex_redact_patterns = [
        # Customer information
        r'DIVYAM\s+RASTOGI',                    # Customer name
        r'xxxx-xxxxxx-\d+',                     # Account number (partial)
        r'Flat\s+\d+',                          # Address start
        r'Golding\s+House,\s+\d+\s+Beaufort\s+Sq', # Full address
        r'London',                              # City
        r'NW\d+[A-Z]+',                        # Postcode
        r'UNITED\s+KINGDOM',                    # Country
        
        # Executive Club and rewards information
        r'Executive\s+Club\s+number:\s*\d+',    # Membership number
        r'01772283',                            # Specific membership number
        r'Card\s+anniversary\s+on\s+\d{2}-\d{2}-\d{4}', # Anniversary date
        
        # Financial amounts and balances
        r'Previous\s+Closing\s+Balance\s*£[\d,]+\.\d{2}',
        r'New\s+Credits\s*£[\d,]+\.\d{2}',
        r'New\s+Debits\s*£[\d,]+\.\d{2}',
        r'Closing\s+Balance\s*£[\d,]+\.\d{2}',
        r'Minimum\s+Repayment\s*£[\d,]+\.\d{2}',
        r'Available\s+Credit\s+limit\s*£[\d,]+\.\d{2}',
        r'Available\s+Cash\s+advance\s+limit\s*£[\d,]+\.\d{2}',
        
        # Rewards amounts
        r'Total\s+Avios\s+earned\s*[\d,]+',
        r'Spend\s+on\s+your\s+Card.*[\d,]+',
        r'Referral\s+bonus\s*[\d,]+',
        r'[\d,]+\s+Avios',                      # Avios amounts
        
        # Statement period dates (specific days, keep month/year)
        r'\d{2}/\d{2}/\d{2}',                   # Date format like 28/07/24
        r'\d{1,2}\s+August\s+\d{4}',           # Payment due date
        
        # Financial summary line items
        r'Previous\s+Closing\s+Balance',
        r'New\s+Credits',
        r'New\s+Debits', 
        r'Closing\s+Balance',
        r'Minimum\s+Repayment',
        r'Available\s+Credit\s+limit',
        r'Available\s+Cash\s+advance\s+limit',
    ]
    
    # Context-specific amount patterns (only redact financial summary amounts)
    amex_amount_patterns = [
        r'£[\d,]+\.\d{2}',  # Will be applied contextually
    ]
    
    # Patterns to preserve (never redact these)
    amex_preserve_patterns = [
        r'American\s+Express',                  # Company name
        r'STATEMENT',                           # Document type
        r'July\s+\d{4}',                       # Statement month/year only
        r'June\s+\d{4}',                       # Statement month/year only
        r'Customer\s+Services',                 # Service info
        r'0800\s+\d+\s+\d+',                   # Customer service numbers
        r'Goods\s+And\s+Services:\s*[\d.]+%',  # Interest rates
        r'Cash\s+Advance:\s*[\d.]+%',          # Interest rates
        r'Balance\s+Transfer:\s*[\d.]+%',      # Interest rates
        r'americanexpress\.co\.uk',            # Website
        r'TFL\s+TRAVEL\s+CHARGE',              # Preserve merchant names for context
        r'TFL\s+GOODWILL',                     # Preserve merchant names for context
    ]
    
    # Get page dimensions to identify financial summary areas vs transaction areas
    page_rect = page.rect
    page_height = page_rect.height
    
    # Define areas where amounts should be redacted (top ~30% of page for AMEX summaries)
    financial_summary_area_max_y = page_height * 0.3
    
    # Process each block
    for block in text_dict["blocks"]:
        if "lines" in block:
            for line in block["lines"]:
                for span in line["spans"]:
                    span_text = span["text"]
                    span_rect = fitz.Rect(span["bbox"])
                    
                    # Skip empty spans
                    if not span_text.strip():
                        continue
                    
                    # Check if this span should be preserved
                    should_preserve = False
                    for preserve_pattern in amex_preserve_patterns:
                        if re.search(preserve_pattern, span_text, re.IGNORECASE):
                            should_preserve = True
                            logger.info(f"Preserving AMEX: '{span_text}' (matches preserve pattern)")
                            break
                    
                    if should_preserve:
                        continue
                    
                    # Check if this span contains financial information to redact
                    should_redact = False
                    matched_pattern = None
                    
                    for pattern in amex_redact_patterns:
                        if re.search(pattern, span_text):
                            should_redact = True
                            matched_pattern = pattern
                            break
                    
                    # Special handling for amounts - only redact in financial summary areas
                    if re.search(r'£[\d,]+\.\d{2}', span_text):
                        # Check if we're in a financial summary area (top of page)
                        if span_rect.y0 <= financial_summary_area_max_y:
                            # Only redact standalone amounts in summary area
                            if re.match(r'^\s*£[\d,]+\.\d{2}\s*$', span_text):
                                should_redact = True
                                matched_pattern = "standalone amount in AMEX summary area"
                        # Skip amount redaction in transaction areas (lower part of page)
                    
                    if should_redact:
                        rect = fitz.Rect(span["bbox"])
                        page.add_redact_annot(rect, fill=(0, 0, 0))
                        redaction_count += 1
                        logger.info(f"Redacting AMEX financial detail: '{span_text}' (pattern: {matched_pattern})")
    
    return redaction_count

def redact_barclaycard_with_privacy(file_path: str, keep_keywords: list, 
                                   output_filename: str, redact_financial: bool = True):
    """
    Enhanced redaction for Barclaycard statements with financial privacy.
    
    Args:
        file_path: Path to input PDF
        keep_keywords: Keywords for transactions to keep
        output_filename: Output filename
        redact_financial: Whether to redact financial details
    
    Returns:
        Tuple of (output_path, total_remaining)
    """
    logger.info(f"Starting enhanced Barclaycard redaction (financial privacy: {redact_financial})")
    
    # First, do the standard transaction redaction
    output_path, total_remaining = redact_pdf_generic(
        file_path, keep_keywords, output_filename, 'barclaycard'
    )
    
    if not redact_financial:
        return output_path, total_remaining
    
    # Now add financial privacy redaction
    logger.info("Adding financial privacy redaction...")
    
    doc = fitz.open(output_path)
    total_financial_redactions = 0
    
    # Apply financial redaction to all pages (pages 1 and 3 have summary info)
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        if page_num in [0, 2]:  # Pages 1 and 3 have financial summary information
            # Apply financial privacy redaction
            redaction_count = redact_financial_details_barclaycard(page)
            total_financial_redactions += redaction_count
            logger.info(f"Applied {redaction_count} financial redactions to page {page_num + 1}")
        
        # Apply redactions
        page.apply_redactions()
    
    # Save the enhanced redacted version with financial privacy
    privacy_output_path = output_path.replace('.pdf', '_privacy.pdf')
    doc.save(privacy_output_path)
    doc.close()
    
    # Remove the intermediate file and rename
    import os
    if os.path.exists(output_path):
        os.remove(output_path)
    os.rename(privacy_output_path, output_path)
    
    logger.info(f"Financial privacy redaction complete. Total financial redactions: {total_financial_redactions}")
    
    return output_path, total_remaining

def redact_amex_with_privacy(file_path: str, keep_keywords: list, 
                            output_filename: str, redact_financial: bool = True):
    """
    Enhanced redaction for AMEX statements with financial privacy.
    
    Args:
        file_path: Path to input PDF
        keep_keywords: Keywords for transactions to keep
        output_filename: Output filename
        redact_financial: Whether to redact financial details
    
    Returns:
        Tuple of (output_path, total_remaining)
    """
    logger.info(f"Starting enhanced AMEX redaction (financial privacy: {redact_financial})")
    
    # First, do the standard transaction redaction
    output_path, total_remaining = redact_pdf_generic(
        file_path, keep_keywords, output_filename, 'amex'
    )
    
    if not redact_financial:
        return output_path, total_remaining
    
    # Now add financial privacy redaction
    logger.info("Adding AMEX financial privacy redaction...")
    
    doc = fitz.open(output_path)
    total_financial_redactions = 0
    
    # Apply financial redaction to specific pages
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # Page 1 has account summary, Page 4 has rewards info
        if page_num in [0, 3]:  
            # Apply financial privacy redaction
            redaction_count = redact_financial_details_amex(page)
            total_financial_redactions += redaction_count
            logger.info(f"Applied {redaction_count} AMEX financial redactions to page {page_num + 1}")
        
        # Apply redactions
        page.apply_redactions()
    
    # Save the enhanced redacted version with financial privacy
    privacy_output_path = output_path.replace('.pdf', '_privacy.pdf')
    doc.save(privacy_output_path)
    doc.close()
    
    # Remove the intermediate file and rename
    import os
    if os.path.exists(output_path):
        os.remove(output_path)
    os.rename(privacy_output_path, output_path)
    
    logger.info(f"AMEX financial privacy redaction complete. Total financial redactions: {total_financial_redactions}")
    
    return output_path, total_remaining

def test_financial_redaction():
    """Test the financial redaction functionality."""
    print("🧪 TESTING FINANCIAL REDACTION")
    print("=" * 60)
    
    # Test with financial redaction
    output_path, total = redact_barclaycard_with_privacy(
        "Barclay.pdf", 
        ["TESCO"], 
        "barclaycard_privacy_test.pdf",
        redact_financial=True
    )
    
    print(f"✅ Enhanced redaction completed")
    print(f"📊 Total remaining: £{total:.2f}")
    print(f"📁 Output: {output_path}")
    
    # Analyze what's preserved vs redacted
    if os.path.exists(output_path):
        doc = fitz.open(output_path)
        page1_text = doc[0].get_text()
        page2_text = doc[1].get_text() if len(doc) > 1 else ""
        
        print(f"\n🔍 VERIFICATION:")
        
        # Check what should be preserved
        preserved_items = [
            ("Mr D M Rastogi", "Customer name"),
            ("July 2025", "Statement month"),
            ("Mastercard", "Card type"),
            ("barclaycard.co.uk", "Website"),
        ]
        
        for item, description in preserved_items:
            visible = item in page1_text
            status = "✅" if visible else "❌"
            print(f"  {status} {description}: {item} ({'visible' if visible else 'redacted'})")
        
        # Check what should be redacted
        redacted_items = [
            ("£1,296.03", "New balance amount"),
            ("£32.76", "Minimum payment"),
            ("£2,821.67", "Previous balance"),
            ("5301 2802 1391 8009", "Card number"),
        ]
        
        # Check what should be preserved (addresses now kept)
        preserved_address_items = [
            ("Flat 48", "Address"),
            ("NW9 5XF", "Postcode"),
        ]
        
        for item, description in redacted_items:
            visible = item in page1_text
            status = "✅" if not visible else "❌"
            print(f"  {status} {description}: {item} ({'redacted' if not visible else 'still visible'})")
        
        # Check addresses should be preserved
        for item, description in preserved_address_items:
            visible = item in page1_text
            status = "✅" if visible else "❌"
            print(f"  {status} {description}: {item} ({'visible' if visible else 'redacted'})")
        
        # Check TESCO transactions
        tesco_visible = "Tesco" in page2_text
        print(f"  {'✅' if tesco_visible else '❌'} TESCO transactions: {'visible' if tesco_visible else 'redacted'}")
        
        doc.close()
        
        # Don't clean up - keep the file for verification
        print(f"📁 Redacted file saved as: {output_path}")
        # os.remove(output_path)
    
    print(f"\n✅ Test complete")

def test_amex_financial_redaction():
    """Test the AMEX financial redaction functionality."""
    print("🧪 TESTING AMEX FINANCIAL REDACTION")
    print("=" * 60)
    
    # Test with financial redaction
    output_path, total = redact_amex_with_privacy(
        "2024-07-28.pdf", 
        ["TFL"], 
        "amex_privacy_test.pdf",
        redact_financial=True
    )
    
    print(f"✅ Enhanced AMEX redaction completed")
    print(f"📊 Total remaining: £{total:.2f}")
    print(f"📁 Output: {output_path}")
    
    # Analyze what's preserved vs redacted
    if os.path.exists(output_path):
        doc = fitz.open(output_path)
        page1_text = doc[0].get_text()
        page4_text = doc[3].get_text() if len(doc) > 3 else ""
        
        print(f"\n🔍 VERIFICATION:")
        
        # Check what should be preserved
        preserved_items = [
            ("American Express", "Company name"),
            ("July 2024", "Statement month"),
            ("TFL TRAVEL CHARGE", "Transaction merchant"),
            ("Customer Services", "Service info"),
        ]
        
        for item, description in preserved_items:
            visible = item in page1_text or item in page4_text
            status = "✅" if visible else "❌"
            print(f"  {status} {description}: {item} ({'visible' if visible else 'redacted'})")
        
        # Check what should be redacted
        redacted_items = [
            ("DIVYAM RASTOGI", "Customer name"),
            ("xxxx-xxxxxx-53008", "Account number"),
            ("Flat 48", "Address"),
            ("01772283", "Executive Club number"),
            ("02-08-2024", "Anniversary date"),
        ]
        
        for item, description in redacted_items:
            visible = item in page1_text or item in page4_text
            status = "✅" if not visible else "❌"
            print(f"  {status} {description}: {item} ({'redacted' if not visible else 'still visible'})")
        
        # Check TFL transactions in other pages
        tfl_visible = False
        for page_num in range(len(doc)):
            if "TFL" in doc[page_num].get_text():
                tfl_visible = True
                break
        
        print(f"  {'✅' if tfl_visible else '❌'} TFL transactions: {'visible' if tfl_visible else 'redacted'}")
        
        doc.close()
        
        # Keep the file for verification
        print(f"📁 Redacted file saved as: {output_path}")
    
    print(f"\n✅ AMEX test complete")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "amex":
        test_amex_financial_redaction()
    else:
        test_financial_redaction()