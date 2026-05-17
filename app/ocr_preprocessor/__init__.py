"""
ocr_preprocessor — Production OCR image preprocessing pipeline.

Optimises high-resolution PDF pages for Gemini Vision OCR by applying an
intelligent chain of image processing operations that reduce token cost by
70–90% with near-zero impact on recognition accuracy.

Quick start
-----------
    from pathlib import Path
    from ocr_preprocessor import OCRPreprocessor, PreprocessingConfig

    preprocessor = OCRPreprocessor()
    metrics = preprocessor.process_pdf(Path("records.pdf"), output_dir=Path("output/"))
    print(metrics.to_json())

In-memory Gemini integration
-----------------------------
    images, metrics = preprocessor.process_pdf_to_memory(Path("records.pdf"))
    # images[i] is JPEG bytes or None if the page was blank/failed
"""

from .config import OutputFormat, PreprocessingConfig
from .metrics import PageMetrics, PipelineMetrics
from .preprocessor import OCRPreprocessor, ProcessedPage
from .utils import estimate_gemini_tokens, format_bytes

__all__ = [
    "OCRPreprocessor",
    "PreprocessingConfig",
    "OutputFormat",
    "ProcessedPage",
    "PageMetrics",
    "PipelineMetrics",
    "format_bytes",
    "estimate_gemini_tokens",
]
