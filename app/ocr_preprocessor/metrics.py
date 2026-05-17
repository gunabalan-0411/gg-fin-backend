"""
Metrics collection and reporting for the OCR preprocessing pipeline.

Provides per-page and aggregate statistics for monitoring, alerting, and
cost estimation.  Designed to be serialisable to JSON for ingestion into
observability systems (Datadog, CloudWatch, BigQuery, etc.).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .utils import estimate_gemini_tokens, format_bytes


@dataclass
class PageMetrics:
    """
    Immutable record of preprocessing results for a single PDF page.

    Attributes are intentionally primitive types so instances can be
    serialised to JSON without a custom encoder.
    """

    page_number: int
    original_size_bytes: int
    optimized_size_bytes: int
    original_dimensions: Tuple[int, int]    # (width, height) px
    optimized_dimensions: Tuple[int, int]   # (width, height) px
    processing_time_ms: float
    was_skipped: bool = False
    skip_reason: Optional[str] = None
    had_error: bool = False

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def compression_ratio(self) -> float:
        """original_bytes / optimized_bytes.  Higher = better compression."""
        if self.optimized_size_bytes == 0:
            return 0.0
        return self.original_size_bytes / self.optimized_size_bytes

    @property
    def size_reduction_pct(self) -> float:
        """Percentage of bytes removed relative to the raw rendered size."""
        if self.original_size_bytes == 0:
            return 0.0
        return (1.0 - self.optimized_size_bytes / self.original_size_bytes) * 100.0

    @property
    def pixel_reduction_pct(self) -> float:
        """Percentage of pixels removed relative to the rendered pixel count."""
        orig = self.original_dimensions[0] * self.original_dimensions[1]
        opt  = self.optimized_dimensions[0] * self.optimized_dimensions[1]
        if orig == 0:
            return 0.0
        return (1.0 - opt / orig) * 100.0

    @property
    def estimated_gemini_tokens_before(self) -> int:
        w, h = self.original_dimensions
        return estimate_gemini_tokens(w, h)

    @property
    def estimated_gemini_tokens_after(self) -> int:
        w, h = self.optimized_dimensions
        return estimate_gemini_tokens(w, h)

    @property
    def token_reduction_pct(self) -> float:
        before = self.estimated_gemini_tokens_before
        if before == 0:
            return 0.0
        after = self.estimated_gemini_tokens_after
        return (1.0 - after / before) * 100.0

    # ── Factory helpers ───────────────────────────────────────────────────────

    @classmethod
    def skipped(
        cls,
        page_number: int,
        raw_size: int,
        original_dims: Tuple[int, int],
        reason: str,
        elapsed_ms: float,
    ) -> "PageMetrics":
        return cls(
            page_number=page_number,
            original_size_bytes=raw_size,
            optimized_size_bytes=0,
            original_dimensions=original_dims,
            optimized_dimensions=(0, 0),
            processing_time_ms=elapsed_ms,
            was_skipped=True,
            skip_reason=reason,
        )

    @classmethod
    def error(cls, page_number: int, elapsed_ms: float = 0.0) -> "PageMetrics":
        return cls(
            page_number=page_number,
            original_size_bytes=0,
            optimized_size_bytes=0,
            original_dimensions=(0, 0),
            optimized_dimensions=(0, 0),
            processing_time_ms=elapsed_ms,
            had_error=True,
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["compression_ratio"]            = round(self.compression_ratio, 3)
        d["size_reduction_pct"]           = round(self.size_reduction_pct, 2)
        d["pixel_reduction_pct"]          = round(self.pixel_reduction_pct, 2)
        d["estimated_tokens_before"]      = self.estimated_gemini_tokens_before
        d["estimated_tokens_after"]       = self.estimated_gemini_tokens_after
        d["token_reduction_pct"]          = round(self.token_reduction_pct, 2)
        return d


@dataclass
class PipelineMetrics:
    """
    Aggregate statistics for an entire PDF preprocessing run.

    Computed from the list of PageMetrics produced by the pipeline.
    Provides both raw numbers and derived KPIs suitable for dashboards.
    """

    pdf_path: Path
    total_pages: int
    processed_pages: int
    skipped_pages: int
    failed_pages: int
    total_original_bytes: int
    total_optimized_bytes: int
    total_pipeline_ms: float
    page_metrics: List[PageMetrics] = field(default_factory=list)

    # Raw ProcessedPage results — stored for in-memory byte access.
    # Excluded from repr and dict serialisation (contains large byte arrays).
    _raw_results: List[Any] = field(default_factory=list, repr=False)

    # ── Derived KPIs ──────────────────────────────────────────────────────────

    @property
    def overall_compression_ratio(self) -> float:
        if self.total_optimized_bytes == 0:
            return 0.0
        return self.total_original_bytes / self.total_optimized_bytes

    @property
    def overall_size_reduction_pct(self) -> float:
        if self.total_original_bytes == 0:
            return 0.0
        return (1.0 - self.total_optimized_bytes / self.total_original_bytes) * 100.0

    @property
    def throughput_pages_per_sec(self) -> float:
        if self.total_pipeline_ms == 0:
            return 0.0
        return self.processed_pages / (self.total_pipeline_ms / 1_000.0)

    @property
    def avg_page_ms(self) -> float:
        if self.processed_pages == 0:
            return 0.0
        return self.total_pipeline_ms / self.processed_pages

    @property
    def total_tokens_before(self) -> int:
        return sum(m.estimated_gemini_tokens_before for m in self.page_metrics if not m.had_error)

    @property
    def total_tokens_after(self) -> int:
        return sum(m.estimated_gemini_tokens_after for m in self.page_metrics if not m.had_error and not m.was_skipped)

    @property
    def token_savings_pct(self) -> float:
        before = self.total_tokens_before
        if before == 0:
            return 0.0
        return (1.0 - self.total_tokens_after / before) * 100.0

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_page_results(
        cls,
        results: List[Any],          # List[ProcessedPage] — avoid circular import
        pipeline_elapsed_ms: float,
        pdf_path: Path,
    ) -> "PipelineMetrics":
        page_metrics = [r.metrics for r in results]

        processed = sum(1 for r in results if not r.was_skipped and r.error is None)
        skipped   = sum(1 for r in results if r.was_skipped)
        failed    = sum(1 for r in results if r.error is not None)

        total_orig = sum(
            m.original_size_bytes
            for m in page_metrics
            if not m.had_error
        )
        total_opt = sum(
            m.optimized_size_bytes
            for m in page_metrics
            if not m.had_error and not m.was_skipped
        )

        inst = cls(
            pdf_path=pdf_path,
            total_pages=len(results),
            processed_pages=processed,
            skipped_pages=skipped,
            failed_pages=failed,
            total_original_bytes=total_orig,
            total_optimized_bytes=total_opt,
            total_pipeline_ms=pipeline_elapsed_ms,
            page_metrics=page_metrics,
        )
        inst._raw_results = results
        return inst

    # ── Reporting ─────────────────────────────────────────────────────────────

    def log_summary(self, logger: logging.Logger) -> None:
        sep = "-" * 62
        logger.info(sep)
        logger.info("  OCR PREPROCESSING PIPELINE - SUMMARY")
        logger.info(sep)
        logger.info("  File            : %s", self.pdf_path.name)
        logger.info("  Pages total     : %d", self.total_pages)
        logger.info("  Pages processed : %d", self.processed_pages)
        logger.info("  Pages skipped   : %d  (blank)", self.skipped_pages)
        logger.info("  Pages failed    : %d", self.failed_pages)
        logger.info(sep)
        logger.info("  Raw size        : %s", format_bytes(self.total_original_bytes))
        logger.info("  Optimised size  : %s", format_bytes(self.total_optimized_bytes))
        logger.info("  Size reduction  : %.1f%%", self.overall_size_reduction_pct)
        logger.info("  Compression     : %.2fx", self.overall_compression_ratio)
        logger.info(sep)
        logger.info("  Tokens before   : %d", self.total_tokens_before)
        logger.info("  Tokens after    : %d", self.total_tokens_after)
        logger.info("  Token savings   : %.1f%%", self.token_savings_pct)
        logger.info(sep)
        logger.info("  Pipeline time   : %.1f ms", self.total_pipeline_ms)
        logger.info("  Avg per page    : %.1f ms", self.avg_page_ms)
        logger.info("  Throughput      : %.2f pages/sec", self.throughput_pages_per_sec)
        logger.info(sep)

    def to_json(self, indent: int = 2) -> str:
        """Serialise to JSON for logging to observability systems."""
        data = {
            "pdf": str(self.pdf_path),
            "pages": {
                "total": self.total_pages,
                "processed": self.processed_pages,
                "skipped": self.skipped_pages,
                "failed": self.failed_pages,
            },
            "size_bytes": {
                "original": self.total_original_bytes,
                "optimized": self.total_optimized_bytes,
                "reduction_pct": round(self.overall_size_reduction_pct, 2),
                "compression_ratio": round(self.overall_compression_ratio, 3),
            },
            "gemini_tokens": {
                "before": self.total_tokens_before,
                "after": self.total_tokens_after,
                "savings_pct": round(self.token_savings_pct, 2),
            },
            "timing_ms": {
                "total_pipeline": round(self.total_pipeline_ms, 1),
                "avg_per_page": round(self.avg_page_ms, 1),
                "throughput_pages_per_sec": round(self.throughput_pages_per_sec, 3),
            },
            "pages": [m.to_dict() for m in self.page_metrics],
        }
        return json.dumps(data, indent=indent, default=str)
