"""
Generic credit card statement redaction module.
Supports multiple providers through configuration system.
"""
import fitz
import re
import os
import logging
from typing import List, Dict, Tuple, Optional
from provider_config import ProviderConfig, get_provider_config, detect_provider

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Legacy TransactionExtractor class removed in favor of simple span-by-span approach
# The simple redact_pdf_generic() function below is more reliable and maintainable

class TransactionExtractor_DEPRECATED:
    """Extract transactions from PDF based on provider configuration."""
    
    def __init__(self, config: ProviderConfig):
        self.config = config
        self.date_patterns = config.get_date_regex()
        self.currency_patterns = config.get_currency_regex()
    
    def find_transaction_section(self, page) -> Optional[float]:
        """Find the y-coordinate where transactions start."""
        text_instances = page.get_text("dict")["blocks"]
        
        for inst in text_instances:
            if 'lines' in inst:
                for line in inst['lines']:
                    for span in line['spans']:
                        text_lower = span['text'].lower()
                        # Check against all possible section headers
                        for header in self.config.section_headers:
                            if header.lower() in text_lower:
                                logger.info(f"Found section header '{header}' at y={span['bbox'][3]}")
                                return span['bbox'][3]
        return None
    
    def is_date(self, text: str) -> bool:
        """Check if text matches any date pattern."""
        for pattern in self.date_patterns:
            if pattern.search(text):
                return True
        return False
    
    def is_amount(self, text: str) -> bool:
        """Check if text matches any currency pattern."""
        for pattern in self.currency_patterns:
            if pattern.search(text):
                return True
        return False
    
    def extract_amount(self, text: str) -> Optional[float]:
        """Extract numeric amount from text."""
        # Try each currency pattern
        for pattern in self.currency_patterns:
            match = pattern.search(text)
            if match:
                # Extract just the numeric part
                amount_str = match.group()
                # Remove currency symbols and convert to float
                amount_str = re.sub(r'[£$€¥₹,]', '', amount_str)
                amount_str = re.sub(r'\s*(USD|GBP|EUR|JPY|INR)', '', amount_str)
                try:
                    return float(amount_str)
                except ValueError:
                    continue
        return None
    
    def extract_transactions_single_line(self, page) -> List[Dict]:
        """Extract transactions assuming single-line format (like AMEX)."""
        transactions = []
        text_instances = page.get_text("dict")["blocks"]
        transaction_start_y = self.find_transaction_section(page)
        
        for inst in text_instances:
            if 'lines' in inst:
                for line in inst['lines']:
                    line_text = ' '.join(span['text'] for span in line['spans'])
                    y_coord = line['spans'][0]['bbox'][1] if line['spans'] else 0
                    
                    # Skip if before transaction section
                    if transaction_start_y and y_coord <= transaction_start_y:
                        continue
                    
                    # Check if line contains both date and amount
                    has_date = self.is_date(line_text)
                    has_amount = self.is_amount(line_text)
                    
                    # For AMEX single-line format, we want lines that have meaningful content
                    # Skip lines that are just dates or just amounts
                    is_meaningful_transaction = (
                        (has_date and has_amount) or  # Complete transaction line
                        (has_date and len(line_text.split()) > 3) or  # Date + description
                        (has_amount and len(line_text.split()) > 2)    # Description + amount
                    )
                    
                    # Also check for transaction-like patterns without strict date/amount requirements
                    # This helps with AMEX transactions that might not match our patterns exactly
                    if not is_meaningful_transaction:
                        # Look for lines that contain common transaction keywords and have reasonable length
                        transaction_keywords = ['tfl', 'travel', 'charge', 'goodwill', 'office', 'supplies']
                        line_lower = line_text.lower()
                        has_transaction_keyword = any(keyword in line_lower for keyword in transaction_keywords)
                        is_reasonable_length = len(line_text.split()) >= 3
                        
                        if has_transaction_keyword and is_reasonable_length:
                            is_meaningful_transaction = True
                    
                    if is_meaningful_transaction:
                        transaction = {
                            'text': line_text,
                            'spans': line['spans'],
                            'has_date': has_date,
                            'has_amount': has_amount,
                            'y_coord': y_coord
                        }
                        amount = self.extract_amount(line_text)
                        if amount:
                            transaction['amount'] = amount
                        else:
                            logger.debug(f"No amount found in transaction: '{line_text}'")
                        transactions.append(transaction)
        
        return transactions
    
    def extract_transactions_multi_line(self, page) -> List[Dict]:
        """Extract transactions assuming multi-line format (like Barclaycard and AMEX)."""
        transactions = []
        text_instances = page.get_text("dict")["blocks"]
        transaction_start_y = self.find_transaction_section(page)
        
        # First pass: collect all text spans with coordinates
        all_spans = []
        for inst in text_instances:
            if 'lines' in inst:
                for line in inst['lines']:
                    for span in line['spans']:
                        y_coord = span['bbox'][1]
                        if transaction_start_y and y_coord <= transaction_start_y:
                            continue
                        all_spans.append({
                            'text': span['text'],
                            'bbox': span['bbox'],
                            'y': y_coord,
                            'x': span['bbox'][0],
                            'span_data': span
                        })
        
        # Sort by Y coordinate (top to bottom)
        all_spans.sort(key=lambda x: x['y'])
        
        current_transaction = None
        
        i = 0
        while i < len(all_spans):
            span = all_spans[i]
            
            # Check if this span contains a date (starts new transaction)
            if self.is_date(span['text']):
                # Check if this date has a corresponding description nearby
                # Look for description spans on the same line or nearby lines
                has_description = False
                
                # First, check for descriptions on the exact same line (within 2 Y points)
                same_line_spans = [s for s in all_spans if abs(s['y'] - span['y']) < 2]
                
                for other_span in same_line_spans:
                    # Look for substantial text that's not a date and further right
                    if (other_span['x'] > span['x'] + 30 and  # To the right of the date
                        not self.is_date(other_span['text']) and
                        len(other_span['text'].strip()) > 5):
                        
                        other_text = other_span['text'].strip()
                        # Must not be just numbers, currency symbols, or headers
                        if (not other_text.replace('.', '').replace(',', '').isdigit() and
                            other_text not in ['Amount', '£', 'GBP', 'USD', 'Process', 'Foreign Spend']):
                            has_description = True
                            break
                
                # If no same-line description, check nearby lines (within 20 points)
                if not has_description:
                    for other_span in all_spans:
                        y_distance = abs(other_span['y'] - span['y'])
                        if (1 < y_distance < 20 and  # Nearby but not same line
                            not self.is_date(other_span['text']) and 
                            len(other_span['text'].strip()) > 5):
                            
                            other_text = other_span['text'].strip()
                            if (not other_text.replace('.', '').replace(',', '').isdigit() and
                                other_text not in ['Amount', '£', 'GBP', 'USD', 'Process', 'Foreign Spend']):
                                has_description = True
                                break
                
                # Only process this date if it has a corresponding description
                if not has_description:
                    i += 1
                    continue
                
                # Save previous transaction if exists
                if current_transaction:
                    transactions.append(current_transaction)
                
                # Start new transaction
                current_transaction = {
                    'date_line': span['text'],
                    'spans': [span['span_data']],
                    'description_lines': [],
                    'y_coord': span['y'],
                    'all_spans': [[span['span_data']]]
                }
                
                # Look for all spans belonging to this transaction
                # Collect all spans on the same line or next few lines
                transaction_spans = [span]
                processed_indices = {i}
                
                # First, collect all spans on the exact same line (same Y coordinate)
                for k in range(len(all_spans)):
                    if k in processed_indices:
                        continue
                    other_span = all_spans[k]
                    
                    # Same line (within 1 point Y tolerance)
                    if abs(other_span['y'] - span['y']) < 1:
                        transaction_spans.append(other_span)
                        processed_indices.add(k)
                
                # Sort spans on the same line by X coordinate (left to right)
                same_line_spans = [s for s in transaction_spans if abs(s['y'] - span['y']) < 1]
                same_line_spans.sort(key=lambda x: x['x'])
                
                # Build transaction from same-line spans
                description_parts = []
                amount_value = None
                amount_spans = []
                
                for tspan in same_line_spans:
                    if not self.is_date(tspan['text']) or tspan == span:  # Include the date span and non-date spans
                        description_parts.append(tspan['text'])
                        
                        # Check for amount
                        amount = self.extract_amount(tspan['text'])
                        if amount:
                            amount_value = amount
                            amount_spans = [tspan['span_data']]
                
                # Check if we have any meaningful content beyond just the date
                meaningful_content = [part for part in description_parts if not self.is_date(part) and len(part.strip()) > 2]
                
                if not meaningful_content:
                    # This is just a standalone date with no description - skip it
                    i += 1
                    continue
                
                current_transaction['description_lines'] = description_parts[1:] if len(description_parts) > 1 else []
                current_transaction['all_spans'] = [[s['span_data'] for s in same_line_spans]]
                
                if amount_value:
                    current_transaction['amount'] = amount_value
                    current_transaction['amount_spans'] = amount_spans
                
                # Look for continuation lines below (within 30 points)
                for k in range(len(all_spans)):
                    if k in processed_indices:
                        continue
                    other_span = all_spans[k]
                    y_distance = other_span['y'] - span['y']
                    
                    # Only look at lines below and within reasonable distance
                    if 1 < y_distance < 30 and not self.is_date(other_span['text']):
                        current_transaction['description_lines'].append(other_span['text'])
                        current_transaction['all_spans'].append([other_span['span_data']])
                        processed_indices.add(k)
                        
                        # Check for amount on continuation lines
                        amount = self.extract_amount(other_span['text'])
                        if amount and not current_transaction.get('amount'):
                            current_transaction['amount'] = amount
                            current_transaction['amount_spans'] = [other_span['span_data']]
                
                # Skip all the spans we've processed
                i = max(processed_indices) if processed_indices else i
            
            i += 1
        
        # Don't forget last transaction
        if current_transaction:
            transactions.append(current_transaction)
        
        # Clean up transactions - remove any that are just standalone dates
        cleaned_transactions = []
        for trans in transactions:
            # Check if transaction has meaningful content beyond just dates
            if 'date_line' in trans:
                full_text = trans['date_line'] + ' ' + ' '.join(trans['description_lines'])
            else:
                full_text = trans.get('text', '')
            
            # Remove dates from the text and check if there's meaningful content left
            text_without_dates = full_text
            for pattern in self.date_patterns:
                text_without_dates = pattern.sub('', text_without_dates)
            
            # Clean up whitespace and check length
            meaningful_text = text_without_dates.strip()
            
            # Only keep transactions that have substantial non-date content
            if len(meaningful_text) > 5 and not meaningful_text.replace('.', '').replace(',', '').isdigit():
                cleaned_transactions.append(trans)
        
        return cleaned_transactions
    
    def extract_transactions(self, page) -> List[Dict]:
        """Extract transactions based on provider configuration."""
        if self.config.transaction_structure == 'single_line':
            return self.extract_transactions_single_line(page)
        elif self.config.transaction_structure == 'multi_line':
            return self.extract_transactions_multi_line(page)
        else:
            # Auto mode - try both and use the one with more results
            single_line = self.extract_transactions_single_line(page)
            multi_line = self.extract_transactions_multi_line(page)
            
            if len(multi_line) > len(single_line) * 1.5:
                logger.info("Auto-detected multi-line transaction format")
                return multi_line
            else:
                logger.info("Auto-detected single-line transaction format")
                return single_line



def _attach_semantic_verdicts(all_spans, keep_keywords, instruction=None):
    """Opt-in TypeSafe Jev pass over one page's transaction spans.

    Clusters the Y-sorted spans into visual lines (within 2 Y points,
    mirroring the same-line test in the redaction loop below), sends every
    line's joined text in ONE batched judgment request, and stamps
    ``span['semantic']`` (matches the user's keyword categories) and
    ``span['instruction']`` (matches the free-text removal instruction)
    True/False onto each span. Spans keep no stamp when the relevant feature
    is off or the batch failed, so the substring whitelist behaves exactly
    as before (fail-open).
    """
    import judgment  # lazy: keeps the default path SDK-free
    wants_keywords = bool(keep_keywords) and judgment.enabled('semantic_keywords')
    wants_instruction = bool(instruction and instruction.strip()) \
        and judgment.enabled('instructions')
    if not wants_keywords and not wants_instruction:
        return

    lines = []
    for s in all_spans:  # already sorted by Y
        if lines and abs(s['y'] - lines[-1]['y']) < 2:
            lines[-1]['spans'].append(s)
        else:
            lines.append({'y': s['y'], 'spans': [s]})
    texts = [' '.join(sp['text'] for sp in ln['spans']).strip() for ln in lines]
    judged = [(i, t) for i, t in enumerate(texts) if len(t) >= 2]
    if not judged:
        return
    judged_texts = [t for _, t in judged]
    if wants_keywords:
        verdicts = judgment.semantic_keep_batch(keep_keywords, judged_texts)
        if verdicts is not None:
            for (i, _), verdict in zip(judged, verdicts):
                for sp in lines[i]['spans']:
                    sp['semantic'] = verdict
    if wants_instruction:
        iverdicts = judgment.semantic_instruction_batch(instruction, judged_texts)
        if iverdicts is not None:
            for (i, _), verdict in zip(judged, iverdicts):
                for sp in lines[i]['spans']:
                    sp['instruction'] = verdict


def redact_pdf_generic(file_path: str, keep_keywords: List[str],
                      output_filename: str, provider: str = 'auto',
                      direction: str = 'keep', instruction: str = None) -> Tuple[str, float]:
    """
    Simple span-by-span redaction approach that works reliably.

    Args:
        file_path: Path to input PDF
        keep_keywords: List of keywords to whitelist
        output_filename: Name for output file
        provider: Provider name or 'auto' for auto-detection
        direction: 'keep' (default) keeps matching spans and redacts the rest;
            'redact' inverts it — matching spans are removed, the rest survive.
            The returned total is always the sum of KEPT amounts.
        instruction: Optional free-text removal sentence; spans whose line the
            Jev instruction judgment matches are always redacted.

    Returns:
        Tuple of (output_path, total_remaining)
    """
    doc = fitz.open(file_path)
    
    # Auto-detect provider if needed
    if provider == 'auto':
        # Get text from first page for detection
        first_page_text = doc[0].get_text() if len(doc) > 0 else ""
        # detect_provider returns None for unknown; preserve AMEX fallback here
        provider = detect_provider(first_page_text) or 'amex_uk'
        logger.info(f"Auto-detected provider: {provider}")
    
    # Get provider configuration for date/amount patterns
    config = get_provider_config(provider)
    date_patterns = config.get_date_regex()
    currency_patterns = config.get_currency_regex()
    
    total_remaining = 0.0
    
    # Process each page
    for page_num in range(len(doc)):
        page = doc[page_num]
        logger.info(f"Processing page {page_num + 1}")
        
        # Find transaction section start
        transaction_start_y = find_transaction_section_simple(page, config.section_headers)
        
        # Get all text spans with coordinates
        text_instances = page.get_text("dict")["blocks"]
        all_spans = []
        
        for inst in text_instances:
            if 'lines' in inst:
                for line in inst['lines']:
                    for span in line['spans']:
                        y_coord = span['bbox'][1]
                        # Only process spans in transaction section
                        if transaction_start_y and y_coord > transaction_start_y:
                            all_spans.append({
                                'span': span,
                                'text': span['text'].strip(),
                                'y': y_coord,
                                'x': span['bbox'][0],
                                'bbox': span['bbox']
                            })
        
        # Sort spans by Y coordinate (top to bottom)
        all_spans.sort(key=lambda x: x['y'])

        # Opt-in semantic keyword matching: one batched Jev request judges
        # each visual line against the user's keywords and removal
        # instruction (no-op by default)
        _attach_semantic_verdicts(all_spans, keep_keywords, instruction)
        
        # Process each span and determine if it should be redacted
        for span_data in all_spans:
            span = span_data['span']
            span_text = span_data['text']
            
            # Skip empty spans
            if not span_text or len(span_text.strip()) < 2:
                continue
            
            # Determine if this span belongs to a whitelisted transaction
            should_keep_span = False
            
            # Check if this span or nearby spans contain whitelisted keywords
            for keyword in keep_keywords:
                if keyword.lower() in span_text.lower():
                    should_keep_span = True
                    break
                
                # Also check spans on the same line (within 2 Y points)
                if not should_keep_span:
                    same_line_spans = [s for s in all_spans 
                                     if abs(s['y'] - span_data['y']) < 2]
                    same_line_text = ' '.join(s['text'] for s in same_line_spans)
                    
                    if keyword.lower() in same_line_text.lower():
                        should_keep_span = True
                        break

            # Semantic verdict for this visual line (stamped only when the
            # Jev feature flag is on and the batch succeeded)
            if not should_keep_span and span_data.get('semantic') is True:
                should_keep_span = True

            # Direction-aware decision: 'keep' preserves matches (whitelist,
            # the historic behaviour); 'redact' removes them and preserves
            # the rest. With NO keywords the base is "spans survive" — only
            # an instruction match removes them.
            if keep_keywords:
                keep_span = (should_keep_span if direction == 'keep'
                             else not should_keep_span)
            else:
                keep_span = True
            if span_data.get('instruction') is True:
                keep_span = False

            # If this span belongs to a kept transaction, add its amount to the total
            if keep_span:
                # Check if this span contains an amount
                for pattern in currency_patterns:
                    match = pattern.search(span_text)
                    if match:
                        # Extract numeric amount
                        amount_str = match.group()
                        amount_str = re.sub(r'[£$€¥₹,]', '', amount_str)
                        amount_str = re.sub(r'\\s*(USD|GBP|EUR|JPY|INR)', '', amount_str)
                        try:
                            amount = float(amount_str)
                            total_remaining += amount
                            logger.info(f"Adding amount £{amount} from span: '{span_text}'")
                        except ValueError:
                            pass
                        break
                
                logger.info(f"Keeping span: '{span_text}'")
            else:
                # Redact this span
                rect = fitz.Rect(span['bbox'])
                page.add_redact_annot(rect, fill=(0, 0, 0))
                logger.info(f"Redacting span: '{span_text}'")
        
        # Apply redactions
        page.apply_redactions()
    
    # Save output
    output_filename = f"{output_filename.split('.')[0]}_{total_remaining:.2f}.pdf"
    output_path = os.path.join(os.path.dirname(file_path), output_filename)
    doc.save(output_path)
    doc.close()
    
    logger.info(f"Total remaining: {total_remaining:.2f}")
    
    return output_path, total_remaining


def find_transaction_section_simple(page, section_headers: List[str]) -> float:
    """Find the Y coordinate where transactions start."""
    text_instances = page.get_text("dict")["blocks"]
    
    for inst in text_instances:
        if 'lines' in inst:
            for line in inst['lines']:
                for span in line['spans']:
                    text_lower = span['text'].lower()
                    # Check against all possible section headers
                    for header in section_headers:
                        if header.lower() in text_lower:
                            logger.info(f"Found section header '{header}' at y={span['bbox'][3]}")
                            return span['bbox'][3]
    return None