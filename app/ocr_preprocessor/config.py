"""
Central configuration for the OCR image preprocessing pipeline.

All tuneable parameters live here. In production, load from environment
variables or a YAML config file and pass a PreprocessingConfig instance
to OCRPreprocessor — no code changes required.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple


class OutputFormat(str, Enum):
    JPEG = "JPEG"   # Recommended: best Gemini compatibility, smallest gray files
    WEBP = "WEBP"   # ~10-15% smaller than JPEG, good browser/API support
    PNG  = "PNG"    # Lossless — only for debug; far too large for production


@dataclass
class PreprocessingConfig:
    """
    Unified configuration for the OCR preprocessing pipeline.

    Defaults are tuned for high-resolution iPhone-captured handwriting PDFs
    targeting Gemini Vision OCR.  Each field documents its rationale and the
    tradeoff it controls so future engineers can tune with confidence.
    """

    # ── PDF Rendering ─────────────────────────────────────────────────────────
    render_dpi: int = 150
    """
    DPI used when rasterising PDF pages via PyMuPDF.

    Rationale
    ---------
    iPhone captures handwriting at effective resolutions equivalent to
    200-300+ DPI.  Gemini Vision does NOT need pixel-perfect reproduction —
    it needs enough spatial resolution to distinguish letterforms.

        DPI   A4 pixels       Gemini 512-tiles   Relative cost
        120   994 × 1406      2 × 3 = 6           1.0×  (minimum safe)
        150   1240 × 1754     3 × 4 = 12          2.0×  ← recommended
        200   1654 × 2339     4 × 5 = 20          3.3×
        300   2480 × 3508     5 × 7 = 35          5.8×  (overkill)

    150 DPI gives sufficient handwriting detail with ~3× fewer tiles than
    the 300 DPI source.  For very small or faint writing, use 180-200 DPI.
    """

    # ── Dimension Clamping ────────────────────────────────────────────────────
    max_long_side: int = 1600
    """
    Hard ceiling on the longest image dimension after rendering (pixels).

    Gemini Vision token cost
    ------------------------
    Each 512×512 tile costs ~258 tokens.  Staying under 1600px on the long
    side caps tiles at 3×4 = 12 per page, regardless of page orientation.

    A 3024×4032 iPhone photo → ~47 tiles (~12,126 tokens).
    After resize to 1200×1600 → 8 tiles (~2,064 tokens).
    That is a ~6× token reduction per page.
    """

    min_long_side: int = 900
    """
    Soft floor — prevents over-shrinking very small embedded pages.
    Upscaling below this threshold is rarely beneficial.
    """

    # ── Grayscale Conversion ──────────────────────────────────────────────────
    convert_to_grayscale: bool = True
    """
    Collapse RGB (3 channels) to luminance (1 channel) before encoding.

    Impact
    ------
    - Raw pixel buffer: 66% smaller
    - JPEG file size:   40-55% smaller vs. colour JPEG at same quality
    - OCR accuracy:     zero degradation — handwriting is monochromatic
    - Gemini Vision:    processes grayscale JPEG correctly as of 2025

    Set False only if colour is semantically meaningful (e.g., colour-coded
    annotations, multi-coloured form fields).
    """

    # ── Contrast Enhancement — CLAHE ──────────────────────────────────────────
    apply_clahe: bool = True
    clahe_clip_limit: float = 2.0
    """
    CLAHE contrast amplification ceiling.

    Low values (1.5–2.5) recover faded ink without amplifying scanner noise.
    High values (>3.0) risk creating visible block artifacts and may darken
    faint marks into illegibility.  2.0 is the production sweet spot.
    """

    clahe_tile_grid_size: Tuple[int, int] = (8, 8)
    """
    CLAHE grid cells.  Smaller tiles = more localised contrast adjustment.
    (8, 8) works well for A4 pages at 150 DPI (≈155×220 pixels per tile).
    """

    # ── Edge-Preserving Noise Reduction ───────────────────────────────────────
    apply_denoising: bool = True
    bilateral_d: int = 5
    """
    Bilateral filter neighbourhood diameter.

    Must be small (5–7) for handwriting.  Larger diameters smear thin pen
    strokes and merge adjacent characters.  d=5 removes sensor/compression
    noise from JPEG-embedded source images without affecting ink edges.
    """

    bilateral_sigma_color: float = 20.0
    """
    Bilateral colour sigma — how different two pixel values can be before
    the filter stops blending them.

    Low (12–25): ink edges remain sharp; paper texture is smoothed.
    High (>40):  filter becomes indiscriminate, blurs fine strokes.
    """

    bilateral_sigma_space: float = 20.0
    """
    Bilateral spatial sigma — effective radius of influence.
    Coupled with `bilateral_d`; increasing this beyond d has no effect.
    """

    # ── Unsharp Mask Sharpening ───────────────────────────────────────────────
    apply_sharpening: bool = True
    unsharp_radius: float = 1.5
    """
    Gaussian blur radius used to construct the unsharp mask.

    Smaller (0.5–1.0): recovers very fine detail (thin pencil strokes).
    Larger (2.0–3.0):  enhances broader edges; may cause haloing on thick ink.
    """

    unsharp_strength: float = 0.40
    """
    Blend weight of the sharpening correction.

    Formula: sharpened = original + strength × (original − blurred)

    0.3–0.5: Crisp letterforms, no visible haloing — recommended range.
    > 0.7:  Aggressive; risk of ringing artefacts on compressed sources.
    """

    # ── Adaptive Thresholding (disabled by default — dangerous) ───────────────
    apply_adaptive_threshold: bool = False
    """
    Binarise the image to pure black/white via local threshold.

    DISABLED BY DEFAULT.  Adaptive thresholding destroys:
      - Pencil marks and light ballpoint strokes (erased as background)
      - Pressure-variation gradients (pen lifts become gaps in letters)
      - Faint printed guidelines (destroyed, then OCR loses line alignment)

    Enable only on scanned typewritten documents with very uniform ink.
    """

    adaptive_block_size: int = 51
    """
    Neighbourhood size for local threshold computation (must be odd).
    Larger blocks handle uneven lighting; smaller blocks track finer contrast.
    """

    adaptive_c: int = 10
    """
    Constant subtracted from computed local mean.  Higher values push more
    pixels to white (background) — tune carefully.
    """

    # ── Blank Page Detection ──────────────────────────────────────────────────
    skip_blank_pages: bool = True
    blank_white_ratio_threshold: float = 0.97
    """
    Pages where >97% of pixels are near-white (>240/255) are skipped.

    Sending blank pages to Gemini is pure token waste.  The 97% threshold
    is conservative enough to retain pages with a single header or signature.
    """

    # ── Output Compression ────────────────────────────────────────────────────
    output_format: OutputFormat = OutputFormat.JPEG
    jpeg_quality: int = 85
    """
    JPEG quantisation quality (1–95).

    Recommended operating range for OCR:
      Quality  File size  Artifact risk  Notes
        80     smallest   low-medium     acceptable for large clear handwriting
        85     balanced   low            ← production default
        90     +20%       negligible     use for very fine / small writing
        95+    +60%       none           use PNG instead

    Below 75, JPEG block artifacts become visible to Gemini Vision and can
    degrade recognition of closed loops (a, d, g, o, p, q).
    """

    webp_quality: int = 82
    """
    WebP quality (1–100).  WebP achieves equivalent perceptual quality to
    JPEG at ~10-15% smaller files.  82 ≈ JPEG 85 in visual quality.
    """

    jpeg_optimize: bool = True
    """
    Run an extra Huffman-optimisation pass on JPEG encoding.
    Cost: ~5-10% extra CPU time.  Savings: 3-6% smaller file.  Always on.
    """

    # ── Parallel Processing ───────────────────────────────────────────────────
    use_parallel_processing: bool = True
    max_workers: int = 4
    """
    ThreadPoolExecutor worker count for parallel page preprocessing.

    PyMuPDF rendering is serialised (fitz Document is not thread-safe).
    OpenCV and NumPy operations release the GIL, so threading is effective
    for the CPU-bound preprocessing stages (CLAHE, bilateral filter, JPEG).

    Recommended: min(cpu_count, 8).  More workers increase memory pressure.
    """

    # ── Debug ─────────────────────────────────────────────────────────────────
    save_debug_images: bool = False
    debug_output_dir: Optional[Path] = None
    """
    When enabled, every preprocessed page is written to debug_output_dir
    as a JPEG regardless of the primary output_dir setting.  Useful for
    tuning configuration parameters without changing the primary pipeline.
    """

    log_level: int = logging.INFO
