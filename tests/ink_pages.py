"""Synthetic notebook pages with known ground truth, for testing the ink detector (no real data is used)."""
from __future__ import annotations

import cv2
import numpy as np

W, H = 1000, 1400


def make_page(
    *, seed: int = 1, rules: bool = True, margin: bool = True, gradient: float = 25.0, noise: float = 4.0,
    ink_gray: int = 40, rule_gray: int = 150, thickness: int = 3, rotate: float = 0.0, words: list[str] | None = None, jpeg_quality: int | None = 70,
):
    """
    Returns (image_bytes, truth_mask, rule_mask, n_words):
      truth_mask - 255 where handwriting was drawn;  rule_mask - 255 where a ruled line / the margin was drawn.
    """
    rng = np.random.default_rng(seed)
    page = np.full((H, W), 238, np.float32)
    xs = np.linspace(-gradient, gradient, W, dtype=np.float32)
    page += xs[None, :]                                          # uneven lighting
    page += np.linspace(8, -8, H, dtype=np.float32)[:, None]

    rule = np.zeros((H, W), np.uint8)
    if rules:
        for y in range(120, H - 60, 38):
            cv2.line(rule, (0, y), (W - 1, y), 255, 1)
    if margin:
        cv2.line(rule, (118, 0), (118, H - 1), 255, 2)
        cv2.line(rule, (124, 0), (124, H - 1), 255, 1)
    page[rule > 0] = np.minimum(page[rule > 0], rule_gray)       # lines are mid-grey, lighter than the pen

    truth = np.zeros((H, W), np.uint8)
    words = ["Selvam", "Kannan", "Lakshmi", "Murugan", "Anbu", "Ravi", "Pandi", "Saroja"] if words is None else words
    amounts = ["500", "300", "200", "1000", "400", "500", "1500", "200"]
    n = 0
    for i, (word, amount) in enumerate(zip(words, amounts)):
        y = 170 + i * 76 + int(rng.integers(-6, 7))
        for text, x, scale in ((word, 150 + int(rng.integers(0, 25)), 1.5), (amount, 760 + int(rng.integers(0, 40)), 1.4)):
            layer = np.zeros((H, W), np.uint8)
            cv2.putText(layer, text, (x, y), cv2.FONT_HERSHEY_SCRIPT_COMPLEX, scale, 255, thickness, cv2.LINE_AA)
            truth |= layer
            n += 1
    # a hand-drawn, wavy underline and a strike-through (long strokes that are ink, not ruled lines)
    pts = np.array([[150 + k * 12, 170 + 8 * 76 + 22 + int(5 * np.sin(k / 3.0))] for k in range(40)], np.int32)
    cv2.polylines(truth, [pts], False, 255, thickness + 1, cv2.LINE_AA)

    page[truth > 0] = ink_gray
    page += rng.normal(0, noise, page.shape).astype(np.float32)
    img = np.clip(page, 0, 255).astype(np.uint8)

    if rotate:
        m = cv2.getRotationMatrix2D((W / 2, H / 2), rotate, 1.0)
        img = cv2.warpAffine(img, m, (W, H), flags=cv2.INTER_LINEAR, borderValue=238)
        truth = cv2.warpAffine(truth, m, (W, H), flags=cv2.INTER_LINEAR)
        rule = cv2.warpAffine(rule, m, (W, H), flags=cv2.INTER_LINEAR)
    if jpeg_quality:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
    else:
        ok, buf = cv2.imencode(".png", img)
    return buf.tobytes(), (truth > 127).astype(np.uint8) * 255, (rule > 127).astype(np.uint8) * 255, n
