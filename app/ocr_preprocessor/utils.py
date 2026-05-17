"""
Utility functions for the OCR preprocessing pipeline.

Kept dependency-free (only NumPy + stdlib) so they can be unit-tested
in isolation without loading OpenCV or PyMuPDF.
"""

from __future__ import annotations

import math
from typing import Tuple

import numpy as np


def is_blank_page(img: np.ndarray, white_ratio_threshold: float = 0.97) -> bool:
    """
    Determine whether an image represents a blank or near-blank page.

    Uses a fast luminance approximation rather than a full colour-space
    conversion to avoid the overhead of a cv2.cvtColor call in the hot path.

    A pixel is considered "white" if its luminance exceeds 240/255 (~94%).
    Pages with blank headers, date stamps, or a single signature still pass
    through because they fall below the 97% threshold.

    Parameters
    ----------
    img:
        RGB (H×W×3) or grayscale (H×W) uint8 array.
    white_ratio_threshold:
        Fraction [0,1] of near-white pixels required to classify as blank.

    Returns
    -------
    bool
        True if the page should be skipped.
    """
    if img.size == 0:
        return True

    if img.ndim == 3:
        # BT.601 luminance coefficients — avoids full cvtColor call
        gray = (
            0.299 * img[:, :, 0].astype(np.float32)
            + 0.587 * img[:, :, 1].astype(np.float32)
            + 0.114 * img[:, :, 2].astype(np.float32)
        ).astype(np.uint8)
    else:
        gray = img

    white_pixels: int = int(np.count_nonzero(gray > 240))
    return (white_pixels / gray.size) > white_ratio_threshold


def compute_target_dimensions(
    width: int,
    height: int,
    max_long_side: int,
    min_long_side: int,
) -> Tuple[int, int]:
    """
    Compute output dimensions that fit within (max_long_side × max_long_side)
    while preserving the original aspect ratio.

    The minimum long-side floor prevents upscaling very small source pages.

    Parameters
    ----------
    width, height:
        Source image dimensions in pixels.
    max_long_side:
        Upper bound on the longest output dimension.
    min_long_side:
        Lower bound — returned unchanged if source is already below this.

    Returns
    -------
    (new_width, new_height) as integers.
    """
    if width <= 0 or height <= 0:
        return width, height

    long_side = max(width, height)

    if long_side <= max_long_side:
        # Already within the allowed range — no resize needed
        return width, height

    scale = max_long_side / long_side
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    return new_w, new_h


def estimate_gemini_tokens(width: int, height: int) -> int:
    """
    Estimate the Gemini Vision token cost for an image.

    Gemini 1.5+ splits images into 512×512 tiles; each tile costs 258 tokens.
    There is an additional 258-token base cost regardless of size.

    This is an approximation — actual tokenisation varies by model version
    and whether the image is resized internally by the API.

    Parameters
    ----------
    width, height:
        Image dimensions sent to the API (post-preprocessing).

    Returns
    -------
    Estimated token count as an integer.
    """
    tiles_w = math.ceil(width / 512)
    tiles_h = math.ceil(height / 512)
    total_tiles = tiles_w * tiles_h + 1   # +1 for base image token
    return total_tiles * 258


def format_bytes(n: int) -> str:
    """Return a human-readable byte count string (e.g. '1.4 MB')."""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))
