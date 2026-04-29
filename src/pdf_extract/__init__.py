"""pdf-extract — structure-aware PDF extraction with on-disk cache."""
from .dispatch import EXTRACT_VERSION, extract, section
from .models import ExtractedDoc, ExtractedFigure, ExtractedReference, ExtractedTable

__version__ = EXTRACT_VERSION

__all__ = [
    "extract",
    "section",
    "ExtractedDoc",
    "ExtractedFigure",
    "ExtractedTable",
    "ExtractedReference",
    "EXTRACT_VERSION",
    "__version__",
]
