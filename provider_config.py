"""
Provider configuration for credit card statement redaction.
Supports American Express and Barclaycard only.
"""
import re
from dataclasses import dataclass
from typing import List, Tuple, Optional


@dataclass
class ProviderConfig:
    """Configuration for a specific credit card provider."""
    name: str
    display_name: str
    date_patterns: List[Tuple[str, str]]  # (pattern, format_name)
    currency_patterns: List[str]
    section_headers: List[str]
    transaction_structure: str  # 'single_line', 'multi_line', or 'auto'
    special_markers: List[str] = None
    amount_position: str = 'auto'  # 'start', 'end', 'auto'
    
    def get_date_regex(self):
        """Get compiled regex patterns for dates."""
        return [re.compile(pattern, re.IGNORECASE) for pattern, _ in self.date_patterns]
    
    def get_currency_regex(self):
        """Get compiled regex patterns for currency amounts."""
        return [re.compile(pattern) for pattern in self.currency_patterns]


# Provider configurations - AMEX and Barclaycard only
PROVIDER_CONFIGS = {
    'amex_uk': ProviderConfig(
        name='amex_uk',
        display_name='American Express',
        date_patterns=[
            (r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\b', 'MMM DD'),
        ],
        currency_patterns=[
            r'£[\d,]+\.\d{2}',  # £ symbol
            r'[\d,]+\.\d{2}'    # Plain decimal
        ],
        section_headers=['Transaction Details', 'Transactions'],
        transaction_structure='multi_line',
        special_markers=['Limit £'],
        amount_position='end_of_line'
    ),
    
    'barclaycard': ProviderConfig(
        name='barclaycard',
        display_name='Barclaycard',
        date_patterns=[
            (r'\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b', 'DD MMM'),
        ],
        currency_patterns=[
            r'£[\d,]+\.\d{2}',  # £ symbol
            r'[\d,]+\.\d{2}'    # Plain decimal
        ],
        section_headers=['Your transactions', "How you've used your card"],
        transaction_structure='multi_line',
        special_markers=[],
        amount_position='end_of_line'
    )
}


def get_provider_config(provider_name: str) -> ProviderConfig:
    """Get configuration for a specific provider."""
    # Handle aliases
    if provider_name == 'amex':
        provider_name = 'amex_uk'
    
    # Return config or default to AMEX if not found
    return PROVIDER_CONFIGS.get(provider_name, PROVIDER_CONFIGS['amex_uk'])


def detect_provider(pdf_text: str) -> Optional[str]:
    """Auto-detect credit card provider from PDF text.

    Returns the provider slug ('amex_uk' / 'barclaycard') when a marker matches,
    or None when no provider can be identified. Callers decide the fallback.
    """
    text_lower = pdf_text.lower()

    # AMEX detection
    if 'american express' in text_lower or 'amex' in text_lower:
        return 'amex_uk'

    # Barclaycard detection
    if 'barclaycard' in text_lower or 'barclays' in text_lower:
        return 'barclaycard'

    # Unknown: callers (process_single_file / redact_pdf_generic) fall back to AMEX
    return None


def get_all_providers() -> List[Tuple[str, str]]:
    """Get all available providers for dropdown."""
    providers = [
        ('auto', 'Auto-detect'),
        ('amex_uk', 'American Express'),
        ('barclaycard', 'Barclaycard')
    ]
    return providers