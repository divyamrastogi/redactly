"""
Credit card provider configuration system for generic statement redaction.
"""
import re
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field


@dataclass
class ProviderConfig:
    """Configuration for a specific credit card provider."""
    name: str
    display_name: str
    date_patterns: List[Tuple[str, str]] = field(default_factory=list)
    currency_patterns: List[str] = field(default_factory=list)
    section_headers: List[str] = field(default_factory=list)
    transaction_structure: str = "auto"  # single_line, multi_line, or auto
    special_markers: List[str] = field(default_factory=list)
    amount_position: str = "auto"  # end_of_line, inline, or auto
    
    def get_date_regex(self) -> List[re.Pattern]:
        """Get compiled regex patterns for dates."""
        return [re.compile(pattern) for pattern, _ in self.date_patterns]
    
    def get_currency_regex(self) -> List[re.Pattern]:
        """Get compiled regex patterns for currency."""
        return [re.compile(pattern) for pattern in self.currency_patterns]


# Provider configurations
PROVIDER_CONFIGS = {
    'amex_uk': ProviderConfig(
        name='amex_uk',
        display_name='American Express (UK)',
        date_patterns=[
            (r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\b', 'MMM DD'),
        ],
        currency_patterns=[
            r'£[\d,]+\.\d{2}',  # £ symbol
            r'[\d,]+\.\d{2}'    # Plain decimal
        ],
        section_headers=['Transaction Details', 'Transactions'],
        transaction_structure='multi_line',  # Fixed: AMEX uses multi-line format
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
    ),
    
    'visa': ProviderConfig(
        name='visa',
        display_name='Visa',
        date_patterns=[
            (r'\d{1,2}/\d{1,2}/\d{2,4}', 'DD/MM/YYYY'),
            (r'\d{1,2}/\d{1,2}', 'DD/MM'),
        ],
        currency_patterns=[
            r'[$£€][\d,]+\.\d{2}',  # Multiple currency symbols
            r'[\d,]+\.\d{2}'        # Plain decimal
        ],
        section_headers=['Transactions', 'Account Activity', 'Purchase Activity'],
        transaction_structure='auto',
        special_markers=[],
        amount_position='auto'
    ),
    
    'mastercard': ProviderConfig(
        name='mastercard',
        display_name='Mastercard',
        date_patterns=[
            (r'\d{1,2}/\d{1,2}/\d{2,4}', 'DD/MM/YYYY'),
            (r'\d{1,2}/\d{1,2}', 'DD/MM'),
        ],
        currency_patterns=[
            r'[$£€][\d,]+\.\d{2}',  # Multiple currency symbols
            r'[\d,]+\.\d{2}'        # Plain decimal
        ],
        section_headers=['Transactions', 'Account Activity', 'Transaction History'],
        transaction_structure='auto',
        special_markers=[],
        amount_position='auto'
    ),
    
    'chase': ProviderConfig(
        name='chase',
        display_name='Chase',
        date_patterns=[
            (r'\d{1,2}/\d{1,2}/\d{2,4}', 'MM/DD/YYYY'),  # US format
            (r'\d{1,2}/\d{1,2}', 'MM/DD'),
        ],
        currency_patterns=[
            r'\$[\d,]+\.\d{2}',  # $ symbol
            r'[\d,]+\.\d{2}'     # Plain decimal
        ],
        section_headers=['Transaction Activity', 'Account Activity', 'Transactions'],
        transaction_structure='auto',
        special_markers=[],
        amount_position='auto'
    ),
    
    'generic': ProviderConfig(
        name='generic',
        display_name='Generic/Other',
        date_patterns=[
            # Month name formats
            (r'\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b', 'DD MMM'),
            (r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\b', 'MMM DD'),
            (r'\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b', 'DD Month YYYY'),
            # Numeric formats
            (r'\d{1,2}/\d{1,2}/\d{2,4}', 'DD/MM/YYYY or MM/DD/YYYY'),
            (r'\d{1,2}-\d{1,2}-\d{2,4}', 'DD-MM-YYYY'),
            (r'\d{4}-\d{2}-\d{2}', 'YYYY-MM-DD'),
            (r'\d{1,2}\.\d{1,2}\.\d{2,4}', 'DD.MM.YYYY'),
        ],
        currency_patterns=[
            r'[$£€¥₹][\d,]+\.\d{2}',     # Common currency symbols
            r'[\d,]+\.\d{2}\s*(?:USD|GBP|EUR|JPY|INR)',  # With currency code
            r'(?:USD|GBP|EUR|JPY|INR)\s*[\d,]+\.\d{2}',  # Currency code first
            r'[\d,]+\.\d{2}'              # Plain decimal
        ],
        section_headers=[
            'transaction', 'payment', 'purchase', 'activity',
            'statement', 'detail', 'summary', 'history'
        ],
        transaction_structure='auto',
        special_markers=[],
        amount_position='auto'
    )
}


def get_provider_config(provider_name: str) -> ProviderConfig:
    """Get configuration for a specific provider."""
    # Handle aliases
    if provider_name == 'amex':
        provider_name = 'amex_uk'
    
    return PROVIDER_CONFIGS.get(provider_name, PROVIDER_CONFIGS['generic'])


def detect_provider(pdf_text: str) -> str:
    """Auto-detect credit card provider from PDF text."""
    text_lower = pdf_text.lower()
    
    # Provider detection rules
    detection_rules = {
        'barclaycard': ['barclaycard', 'barclays'],
        'amex_uk': ['american express', 'amex', 'transaction details'],
        'chase': ['chase bank', 'jpmorgan chase', 'chase '],
        'visa': ['visa card', 'visa credit'],
        'mastercard': ['mastercard', 'master card'],
    }
    
    for provider, keywords in detection_rules.items():
        if any(keyword in text_lower for keyword in keywords):
            return provider
    
    # Check for UK-specific patterns (£ symbol)
    if '£' in pdf_text:
        # Could be AMEX UK or Barclaycard
        if 'your transactions' in text_lower:
            return 'barclaycard'
        elif 'transaction details' in text_lower:
            return 'amex_uk'
    
    return 'generic'


def get_all_providers() -> List[Tuple[str, str]]:
    """Get list of all providers for UI dropdown."""
    providers = [('auto', 'Auto-detect')]
    
    # Add providers with proper aliases for UI
    for config in PROVIDER_CONFIGS.values():
        if config.name == 'amex_uk':
            # Use 'amex' as the value for better UX
            providers.append(('amex', config.display_name))
        else:
            providers.append((config.name, config.display_name))
    
    return providers