"""
Ink detection for the OCR page's scan animation (OpenCV): finds handwriting, ignores ruled lines, margins, paper tone and the book
cover.   Synthetic pages with known ground truth (tests/ink_pages.py); no real data.   IN-01 … IN-10 (B7-01 … B7-03, T7-01 … T7-04)
"""
import base64
import time

import cv2
import numpy as np
import pytest

from app.services import ink_service as ink
from tests.ink_pages import H, W, make_page


def decode_mask(result) -> np.ndarray:
    m = cv2.imdecode(np.frombuffer(base64.b64decode(result["mask_png_b64"]), np.uint8), cv2.IMREAD_GRAYSCALE)
    assert m is not None
    return m


def measure(kind: dict):
    """(recall of handwriting, share of ruled-line pixels that leaked, stray ink pixels, result)."""
    image, truth, rule, _n = make_page(**kind)
    res = ink.detect_ink(image)
    mask = decode_mask(res)
    size = (mask.shape[1], mask.shape[0])
    truth = cv2.resize(truth, size, interpolation=cv2.INTER_NEAREST)
    rule = cv2.resize(rule, size, interpolation=cv2.INTER_NEAREST)
    near_ink = cv2.dilate(truth, np.ones((5, 5), np.uint8)) > 0
    recall = ((mask > 0) & (truth > 0)).sum() / max(1, (truth > 0).sum())
    rules_only = (rule > 0) & ~near_ink
    leak = ((mask > 0) & rules_only).sum() / max(1, rules_only.sum())
    stray = int(((mask > 0) & ~near_ink).sum())
    return recall, leak, stray, res


PAGES = {
    "plain": {},
    "skewed +1.5": {"rotate": 1.5},
    "skewed -2": {"rotate": -2.0},
    "faint pencil, ruled lines as dark as the ink": {"ink_gray": 150, "rule_gray": 150},
    "dark ruled lines": {"rule_gray": 90},
    "lossless png": {"jpeg_quality": None},
    "heavy jpeg": {"jpeg_quality": 30},
    "noisy": {"noise": 9},
    "thin pen": {"thickness": 2},
    "strong uneven lighting": {"gradient": 70},
}


# ── B7-01 / T7-01: finds the handwriting, only the handwriting ───────────────────────────────────────────────
@pytest.mark.parametrize("name", list(PAGES))
def test_finds_the_handwriting_and_nothing_else(name):
    recall, leak, stray, res = measure(PAGES[name])
    assert recall >= 0.97, f"{name}: only {recall:.2f} of the handwriting was found"
    assert leak <= 0.02, f"{name}: {leak:.3f} of the ruled / margin lines were taken for ink"
    assert stray <= 150, f"{name}: {stray} stray ink pixels away from any handwriting"
    assert 15 <= len(res["blobs"]) <= 30                                    # 17 words / numbers (a cursive word is one blob)


def test_ruled_lines_and_the_margin_alone_are_not_ink():
    # a ruled page with dark lines and nothing written but the one hand-drawn underline (a pen stroke, so it IS ink)
    res = ink.detect_ink(make_page(words=[], rule_gray=60)[0])
    mask = decode_mask(res)
    assert len(res["blobs"]) == 1
    x, y, w, h = res["blobs"][0]
    outside = mask.copy()
    outside[max(0, y - 6):y + h + 6, max(0, x - 6):x + w + 6] = 0
    assert outside.sum() == 0                                                  # no ruled line, no margin anywhere else

def test_a_stroke_crossing_a_ruled_line_stays_whole():
    image, truth, _rule, _n = make_page(rule_gray=90)
    mask = decode_mask(ink.detect_ink(image))
    truth = cv2.resize(truth, (mask.shape[1], mask.shape[0]), interpolation=cv2.INTER_NEAREST)
    # rows where a ruled line passes under the handwriting: the handwriting there is still all detected
    for y in range(120, H - 60, 38):
        yy = int(y * mask.shape[0] / H)
        row_truth = truth[yy] > 0
        if row_truth.sum() > 5:
            assert ((mask[yy] > 0) & row_truth).sum() / row_truth.sum() > 0.9


# ── blank / flat pages give nothing ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("gray", [255, 238, 180, 120])
def test_a_blank_or_evenly_coloured_page_has_no_ink(gray):
    rng = np.random.default_rng(0)
    page = np.clip(np.full((H, W), gray, np.float32) + rng.normal(0, 5, (H, W)), 0, 255).astype(np.uint8)
    res = ink.detect_ink(cv2.imencode(".jpg", page)[1].tobytes())
    assert res["blobs"] == [] and res["coverage"] == 0.0


def test_a_gentle_lighting_gradient_is_not_ink():
    xs = np.linspace(90, 250, W, dtype=np.float32)[None, :].repeat(H, 0)
    res = ink.detect_ink(cv2.imencode(".png", xs.astype(np.uint8))[1].tobytes())
    assert res["blobs"] == []


def test_the_book_cover_beside_the_page_is_not_ink():
    image, _t, _r, _n = make_page()
    page = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_GRAYSCALE)
    rng = np.random.default_rng(3)
    cover = rng.integers(10, 90, (H, 110)).astype(np.uint8)                  # dark textured cover on the right
    cover[:, ::7] = 140
    page[:, W - 110:] = cover
    res = ink.detect_ink(cv2.imencode(".png", page)[1].tobytes())
    mask = decode_mask(res)
    assert mask[:, int(mask.shape[1] * 0.9):].sum() == 0                     # nothing on the cover
    assert len([b for b in res["blobs"] if b[0] > mask.shape[1] * 0.9]) == 0
    assert 15 <= len(res["blobs"]) <= 30                                      # and the handwriting is all still there


# ── B7-02: the blobs ─────────────────────────────────────────────────────────────────────────────────────────
def test_blobs_are_boxes_around_ink_ordered_by_bottom_edge_within_the_image():
    res = ink.detect_ink(make_page()[0])
    mask = decode_mask(res)
    bottoms = [y + h for _x, y, _w, h in res["blobs"]]
    assert bottoms == sorted(bottoms)
    for x, y, w, h in res["blobs"]:
        assert 0 <= x and 0 <= y and w > 0 and h > 0 and x + w <= res["width"] and y + h <= res["height"]
        assert (mask[y:y + h, x:x + w] > 0).sum() >= 14                       # every box really holds ink
        assert w * h < 0.3 * res["width"] * res["height"]


def test_each_word_is_found_where_it_was_written():
    image, truth, _rule, _n = make_page(rotate=0.0)
    res = ink.detect_ink(image)
    sx, sy = res["width"] / W, res["height"] / H
    ys, xs = np.nonzero(truth)
    # one blob covers the first word "Selvam" (written near x 150-330, y 120-190)
    word = truth[120:200, 140:340]
    wy, wx = np.nonzero(word)
    cx, cy = (wx.mean() + 140) * sx, (wy.mean() + 120) * sy
    assert any(x <= cx <= x + w and y <= cy <= y + h for x, y, w, h in res["blobs"])


def test_the_number_of_blobs_is_capped():
    page = np.full((H, W), 238, np.uint8)
    for yy in range(40, H - 40, 18):
        for xx in range(40, W - 40, 18):
            cv2.rectangle(page, (xx, yy), (xx + 8, yy + 8), 30, -1)               # 3700 separate 9 x 9 px marks
    res = ink.detect_ink(cv2.imencode(".png", page)[1].tobytes())
    assert len(res["blobs"]) == ink.MAX_BLOBS

# ── B7-03: the result's shape ───────────────────────────────────────────────────────────────────────────────
def test_a_big_photo_is_reduced_and_the_coordinates_follow():
    image, _t, _r, _n = make_page()
    big = cv2.resize(cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_GRAYSCALE), (W * 3, H * 3), interpolation=cv2.INTER_CUBIC)
    res = ink.detect_ink(cv2.imencode(".jpg", big)[1].tobytes())
    assert max(res["width"], res["height"]) == ink.WORK_LONG_SIDE
    assert abs(res["width"] / res["height"] - W / H) < 0.01
    assert 15 <= len(res["blobs"]) <= 30
    assert all(x + w <= res["width"] and y + h <= res["height"] for x, y, w, h in res["blobs"])


def test_the_mask_png_is_binary_and_as_big_as_stated_and_small():
    res = ink.detect_ink(make_page()[0])
    mask = decode_mask(res)
    assert (mask.shape[1], mask.shape[0]) == (res["width"], res["height"])
    assert set(np.unique(mask)) <= {0, 255}
    assert len(res["mask_png_b64"]) < 120_000
    assert 0 < res["coverage"] < 0.1


def test_the_same_page_gives_the_same_answer():
    image = make_page()[0]
    a, b = ink.detect_ink(image), ink.detect_ink(image)
    assert a == b


def test_something_that_is_not_an_image_is_refused():
    with pytest.raises(ValueError):
        ink.detect_ink(b"this is not a picture")
    with pytest.raises(ValueError):
        ink.detect_ink(b"")


def test_it_is_fast_enough_to_run_while_the_page_is_on_screen():
    image = make_page()[0]
    ink.detect_ink(image)                                                      # warm-up
    t = time.perf_counter()
    ink.detect_ink(image)
    assert time.perf_counter() - t < 3.0                                       # about 0.3 s normally; generous for a loaded machine


# ── the small cache ──────────────────────────────────────────────────────────────────────────────────────────
def test_the_cache_computes_once_per_page_and_forgets_the_oldest():
    ink._cache.clear()
    image = make_page()[0]
    calls = []

    def fn():
        calls.append(1)
        return image

    a = ink.cached_ink(("s", 0), fn)
    b = ink.cached_ink(("s", 0), fn)
    assert a is b and len(calls) == 1
    for i in range(1, ink.CACHE_SIZE + 2):
        ink.cached_ink(("s", i), lambda: image)
    assert ("s", 0) not in ink._cache and len(ink._cache) == ink.CACHE_SIZE
    ink._cache.clear()


def test_specks_and_dust_are_not_characters():
    page = np.full((H, W), 238, np.uint8)
    for x, y in ((200, 300), (500, 700), (800, 1000), (300, 1200)):
        cv2.rectangle(page, (x, y), (x + 4, y + 4), 30, -1)                    # 5 x 5 px marks
    res = ink.detect_ink(cv2.imencode(".png", page)[1].tobytes())
    assert res["blobs"] == []


def test_a_light_pencil_scrawl_that_touches_firm_pen_strokes_is_kept_with_them():
    page = np.full((H, W), 238, np.uint8)
    cv2.putText(page, "Selvam", (150, 300), cv2.FONT_HERSHEY_SCRIPT_COMPLEX, 2.0, 40, 4, cv2.LINE_AA)           # firm pen
    cv2.line(page, (150, 330), (420, 330), 120, 3, cv2.LINE_AA)                                             # fainter underline touching the word
    cv2.line(page, (300, 300), (300, 330), 120, 3, cv2.LINE_AA)
    res = ink.detect_ink(cv2.imencode(".png", page)[1].tobytes())
    mask = decode_mask(res)
    sx = res["width"] / W
    assert mask[int(330 * sx) - 3:int(330 * sx) + 4, int(200 * sx):int(400 * sx)].sum() > 0                    # the touching faint line is part of it

def test_a_faint_smudge_that_touches_no_firm_stroke_is_not_ink():
    page = np.full((H, W), 238, np.uint8)
    cv2.putText(page, "Selvam", (150, 300), cv2.FONT_HERSHEY_SCRIPT_COMPLEX, 2.0, 40, 4, cv2.LINE_AA)
    cv2.circle(page, (700, 900), 28, 150, -1)                                   # a pale stain, far from any pen stroke
    res = ink.detect_ink(cv2.imencode(".png", page)[1].tobytes())
    mask = decode_mask(res)
    sx = res["width"] / W
    assert mask[int(850 * sx):int(950 * sx), int(650 * sx):int(750 * sx)].sum() == 0
    assert len(res["blobs"]) >= 1                                               # the word is still there


def test_a_short_flat_dash_that_is_not_a_character_is_not_a_blob():
    page = np.full((H, W), 238, np.uint8)
    cv2.line(page, (200, 600), (330, 600), 40, 3, cv2.LINE_AA)                 # 130 px long, 3 px thick: what a removed ruled line leaves behind
    res = ink.detect_ink(cv2.imencode(".png", page)[1].tobytes())
    assert res["blobs"] == []


def _noisy_word_page():
    rng = np.random.default_rng(1)
    page = np.clip(238 + rng.normal(0, 3.0, (H, W)), 0, 255).astype(np.uint8)       # (with a little paper noise Otsu settles near 92)
    cv2.putText(page, "Selvam", (150, 300), cv2.FONT_HERSHEY_SCRIPT_COMPLEX, 2.0, 40, 4, cv2.LINE_AA)
    return page


def _region(res, mask, x0, y0, x1, y1):
    sx, sy = res["width"] / W, res["height"] / H
    return mask[int(y0 * sy):int(y1 * sy), int(x0 * sx):int(x1 * sx)]


def test_a_stain_paler_than_firm_ink_is_dropped_when_it_touches_no_firm_stroke():
    page = _noisy_word_page()
    cv2.circle(page, (700, 900), 12, 155, -1)                                       # darker than faint, paler than the firm threshold
    res = ink.detect_ink(cv2.imencode(".png", page)[1].tobytes())
    mask = decode_mask(res)
    assert _region(res, mask, 660, 860, 740, 940).sum() == 0
    assert len(res["blobs"]) >= 1                                                    # the word is still found


def test_a_faint_stroke_touching_a_firm_one_is_kept():
    page = _noisy_word_page()
    cv2.line(page, (160, 335), (400, 335), 155, 4, cv2.LINE_AA)                      # a fainter underline ...
    cv2.line(page, (200, 300), (200, 335), 40, 3, cv2.LINE_AA)                       # ... joined to the word by a firm stroke
    res = ink.detect_ink(cv2.imencode(".png", page)[1].tobytes())
    mask = decode_mask(res)
    assert _region(res, mask, 300, 330, 400, 341).sum() > 0


def test_ink_showing_through_from_the_other_side_is_not_taken_for_handwriting():
    page = _noisy_word_page()
    cv2.circle(page, (700, 900), 12, 172, -1)                                       # a pale blot (page back showing through) ...
    cv2.circle(page, (700, 900), 2, 40, -1)                                         # ... with one dark speck inside, so it touches firm ink
    res = ink.detect_ink(cv2.imencode(".png", page)[1].tobytes())
    mask = decode_mask(res)
    assert _region(res, mask, 660, 860, 740, 940).sum() == 0


def test_marks_just_inside_the_border_of_the_scan_are_ignored():
    page = _noisy_word_page()
    cv2.rectangle(page, (400, 5), (440, 13), 30, -1)                                # a shadow strip a few pixels inside the top border
    cv2.rectangle(page, (W - 14, 600), (W - 6, 640), 30, -1)                        # and the right border
    res = ink.detect_ink(cv2.imencode(".png", page)[1].tobytes())
    sy = res["height"] / H
    assert res["blobs"] and all(y > 20 * sy and x + w < res["width"] - 15 for x, y, w, _h in res["blobs"])


def test_marks_on_the_very_edge_of_the_scan_are_ignored():
    page = _noisy_word_page()
    cv2.rectangle(page, (400, 0), (440, 9), 30, -1)
    cv2.rectangle(page, (W - 9, 600), (W - 1, 640), 30, -1)
    res = ink.detect_ink(cv2.imencode(".png", page)[1].tobytes())
    sy = res["height"] / H
    assert res["blobs"] and all(y > 20 * sy and x + w < res["width"] - 15 for x, y, w, _h in res["blobs"])
