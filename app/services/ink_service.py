"""
Ink detection for the OCR page's "reading" animation (OpenCV).

Given the (preprocessed) page image it answers: which pixels are ink, and which characters are there?

    paper estimate  : morphological closing with a kernel wider than a pen stroke removes the ink, leaving the paper (and its
                      uneven lighting) -> smoothed
    subtract        : paper - page  ->  how much darker than the paper each pixel is (the ink signal)
    threshold       : Otsu, but never below a floor (a blank or evenly grey page must give NO ink), with hysteresis so faint
                      pencil strokes connected to firm ones are kept
    ruled lines     : thin, long, straight structures (the notebook's lines, in a slightly rotated sweep so a skewed page works)
                      and the long margin line are removed; pen strokes are thicker and survive, even where they cross a line
    characters      : the mask is dilated a little so the strokes of one character join, then connected components give one
                      box per character (Tamil ligatures stay together)

Nothing is stored: the result is computed from the session's page image on request (and kept in a tiny in-memory cache).
"""
from __future__ import annotations

import base64
import threading
from collections import OrderedDict
from typing import Any

import cv2
import numpy as np

WORK_LONG_SIDE = 1200          # the working image (and the coordinates returned) never exceed this on the long side
MIN_DIFF = 22                  # darker-than-paper by less than this is never ink (blank / evenly grey pages give nothing)
EDGE_FRACTION = 0.012          # scan edges and shadows: ignore this fraction of every border
MAX_BLOBS = 2500
CACHE_SIZE = 12

_cache: "OrderedDict[tuple[str, int], dict]" = OrderedDict()
_cache_lock = threading.Lock()


def _odd(n: float) -> int:
    n = int(round(n))
    return n if n % 2 == 1 else n + 1


def _ellipse(k: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


def _resize(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    long_side = max(h, w)
    if long_side <= WORK_LONG_SIDE:
        return gray
    scale = WORK_LONG_SIDE / long_side
    return cv2.resize(gray, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)


def _long_straight(thin: np.ndarray, length: int, vertical: bool) -> np.ndarray:
    """
    Pixels of `thin` that lie on a straight run of at least `length` px, horizontal (or vertical) within +/- 2.5 degrees.
    Works at half size (a fast 1-px-high rectangle opening per small rotation lets a slightly skewed page work), so it stays cheap.
    """
    src = cv2.dilate(thin, np.ones((3, 3), np.uint8))                      # a 1-px line survives resampling as a 3-px band
    if vertical:
        src = np.ascontiguousarray(src.T)
    full_h, full_w = src.shape
    src = cv2.resize(src, (max(8, full_w // 2), max(8, full_h // 2)), interpolation=cv2.INTER_AREA)
    src = (src > 60).astype(np.uint8) * 255
    h, w = src.shape
    length = max(8, min(length // 2, w - 2))
    rect = cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1))
    gap = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1))
    found = np.zeros_like(src)
    for angle in (-2.0, 0.0, 2.0):
        m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        rot = (cv2.warpAffine(src, m, (w, h), flags=cv2.INTER_LINEAR) > 100).astype(np.uint8) * 255
        rot = cv2.morphologyEx(rot, cv2.MORPH_CLOSE, gap)                  # a line broken into dashes by noise is still one line
        opened = cv2.morphologyEx(rot, cv2.MORPH_OPEN, rect)
        if opened.any():
            back = cv2.warpAffine(opened, m, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
            found |= (back > 100).astype(np.uint8) * 255
    found = cv2.resize(found, (full_w, full_h), interpolation=cv2.INTER_NEAREST)
    return np.ascontiguousarray(found.T) if vertical else found

def _paper(gray: np.ndarray, k: int) -> np.ndarray:
    """The paper under the ink (with its uneven lighting). Done at 1/4 size: the closing is slow on a full-size image and the paper is smooth."""
    h, w = gray.shape
    small = cv2.resize(gray, (max(8, w // 4), max(8, h // 4)), interpolation=cv2.INTER_AREA)
    ks = _odd(max(5, k / 4))
    small = cv2.morphologyEx(small, cv2.MORPH_CLOSE, _ellipse(ks))
    small = cv2.GaussianBlur(small, (0, 0), ks / 3)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _page_region(gray: np.ndarray, level: float) -> np.ndarray:
    """True inside the sheet of paper: the biggest bright region, holes (ink, stains) filled, shrunk a little from its edge."""
    h, w = gray.shape
    bright = (cv2.GaussianBlur(gray, (0, 0), 3) > 0.6 * level).astype(np.uint8)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return np.ones((h, w), bool)
    biggest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(biggest) < 0.3 * h * w:                          # not a recognisable sheet (a close-up?): use the whole image
        return np.ones((h, w), bool)
    region = np.zeros((h, w), np.uint8)
    cv2.drawContours(region, [biggest], -1, 1, thickness=cv2.FILLED)
    shrink = _odd(max(3, 0.012 * max(h, w)))
    return cv2.erode(region, np.ones((shrink, shrink), np.uint8)) > 0

def ink_mask(gray: np.ndarray) -> np.ndarray:
    """Binary ink mask (255 = ink) for a grayscale page image no larger than WORK_LONG_SIDE."""
    h, w = gray.shape
    long_side = max(h, w)

    # 1. the paper: closing removes everything narrower than the kernel that is darker than its surroundings
    paper = _paper(gray, _odd(max(15, 0.03 * long_side)))

    # 2. subtract: how much darker than the paper is each pixel
    diff = cv2.subtract(paper, gray)

    # nothing outside the sheet itself: the book cover, the spine, a table under the page
    diff[~_page_region(gray, float(np.percentile(paper, 70)))] = 0
    if int(diff.max()) < MIN_DIFF:
        return np.zeros_like(gray)

    # 3. thresholds with a floor (a blank page gives nothing): firm ink, and the fainter band around / between firm strokes
    otsu, _ = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    high = max(float(otsu), float(MIN_DIFF))
    low = max(MIN_DIFF * 0.7, high * 0.65)
    weak = (diff >= low).astype(np.uint8) * 255
    strong = diff >= high

    # scan edges / shadows
    ex, ey = max(1, int(w * EDGE_FRACTION)), max(1, int(h * EDGE_FRACTION))
    weak[:ey, :] = 0
    weak[-ey:, :] = 0
    weak[:, :ex] = 0
    weak[:, -ex:] = 0

    # 4. ruled lines and the margin line: thin (<= ~4 px, even after JPEG blur) AND long AND straight. Pen strokes are thicker and stay, even where
    #    they cross a line. Done on the raw threshold, before anything can widen the lines.
    thick = cv2.morphologyEx(weak, cv2.MORPH_OPEN, _ellipse(5))
    thin = cv2.subtract(weak, thick)
    lines = _long_straight(thin, int(0.35 * w), vertical=False) | _long_straight(thin, int(0.35 * h), vertical=True)
    if lines.any():
        weak = cv2.subtract(weak, cv2.subtract(cv2.dilate(lines, np.ones((3, 3), np.uint8)), thick))

    # 5. hysteresis: a faint stroke is kept only when it touches a firm one
    n, labels = cv2.connectedComponents(weak, connectivity=8)
    if n <= 1:
        return np.zeros_like(gray)
    keep = np.zeros(n, bool)
    keep[np.unique(labels[strong & (weak > 0)])] = True
    keep[0] = False
    mask = (keep[labels]).astype(np.uint8) * 255

    # thin tails left where a removed line met a stroke, and 1-2 px fragments: open, then give the surviving strokes their edges back
    core = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3)))
    mask = cv2.bitwise_and(cv2.dilate(core, np.ones((3, 3), np.uint8)), mask)

    # 6. what is not handwriting, per connected piece (all vectorised: a real page has hundreds of pieces)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return mask
    area = stats[:, cv2.CC_STAT_AREA].astype(np.float64)
    cx, cy = stats[:, cv2.CC_STAT_LEFT], stats[:, cv2.CC_STAT_TOP]
    cw, ch = stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT]
    mean_diff = np.bincount(labels.ravel(), weights=diff.ravel().astype(np.float64), minlength=n) / np.maximum(area, 1)
    margin_x, margin_y = int(0.03 * w), int(0.03 * h)
    touches_edge = (cx <= margin_x) | (cy <= margin_y) | (cx + cw >= w - margin_x) | (cy + ch >= h - margin_y)
    bad = (
        (cw >= 0.8 * w) & (ch <= 0.015 * long_side)                       # a full-width rule drawn in thicker ink
        | (ch >= 0.6 * h) & (cw <= 0.015 * long_side)                      # a tall margin line
        | (area < 6)                                                       # specks
        | (mean_diff < 0.8 * high)                                         # faint: ink showing through from the other side of the page
        | (touches_edge & ((area >= 0.02 * h * w) | (ch >= 0.4 * h) | (cw >= 0.4 * w)))   # the cover / spine / a shadow along the edge
    )
    bad[0] = False
    mask[bad[labels]] = 0
    return mask

def ink_blobs(mask: np.ndarray) -> list[list[int]]:
    """One [x, y, w, h] box per character (strokes joined by a small dilation), ordered by bottom edge."""
    h, w = mask.shape
    k = _odd(max(3, 0.004 * max(h, w)))
    joined = cv2.dilate(mask, _ellipse(k))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    if n <= 1:
        return []
    ink_px = np.bincount(labels[mask > 0], minlength=n)                       # real ink pixels per piece (the dilation does not count)
    blobs: list[tuple[int, list[int]]] = []
    page_area = h * w
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        if ink_px[i] < 25 or area > 0.3 * page_area:                           # dust and specks; or something as big as the page
            continue
        if bh <= 9 and bw >= 8 * bh:                                    # a flat sliver: what is left of a ruled line
            continue
        blobs.append((int(ink_px[i]), [x, y, bw, bh]))
    if len(blobs) > MAX_BLOBS:                                       # keep the biggest, a page never has this many characters
        blobs = sorted(blobs, key=lambda b: -b[0])[:MAX_BLOBS]
    return sorted((b for _a, b in blobs), key=lambda b: (b[1] + b[3], b[0]))

def detect_ink(image_bytes: bytes) -> dict[str, Any]:
    """Decode a page image and return {width, height, mask_png_b64, blobs, coverage} (all coordinates in the returned size)."""
    if not image_bytes:
        raise ValueError("The page is not a readable image")
    arr = np.frombuffer(image_bytes, np.uint8)
    gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError("The page is not a readable image")
    gray = _resize(gray)
    mask = ink_mask(gray)
    blobs = ink_blobs(mask)
    h, w = mask.shape
    ok, png = cv2.imencode(".png", mask, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not ok:
        raise ValueError("Could not encode the ink mask")
    return {
        "width": int(w),
        "height": int(h),
        "mask_png_b64": base64.b64encode(png.tobytes()).decode(),
        "blobs": blobs,
        "coverage": round(float((mask > 0).mean()), 4),
    }


def cached_ink(key: tuple[str, int], image_bytes_fn) -> dict[str, Any]:
    """`detect_ink` for a (session, page), remembered for a few pages (the animation asks once per page, but a reload asks again)."""
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    result = detect_ink(image_bytes_fn())
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return result
