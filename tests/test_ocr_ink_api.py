"""GET /api/ocr/page/{session}/{page}/ink: the ink of a page for the scan animation.   IN-11 … IN-13 (B7-04, T7-05)"""
import pytest
from fastapi.testclient import TestClient

from app.services import ink_service, ocr_service
from tests.ink_pages import make_page

URL = "/api/ocr/page/{sid}/{page}/ink"


@pytest.fixture()
def page_bytes(monkeypatch):
    ink_service._cache.clear()
    image = make_page()[0]
    calls = []

    def fake(session_id, page_index):
        calls.append((session_id, page_index))
        if session_id == "gone":
            raise FileNotFoundError(session_id)
        if session_id == "corrupt":
            return b"not an image", "image/jpeg"
        return image, "image/jpeg"

    monkeypatch.setattr(ocr_service, "_get_page_bytes", fake)
    yield calls
    ink_service._cache.clear()


def test_returns_the_ink_of_the_page(client, page_bytes):
    r = client.get(URL.format(sid="abc", page=0))
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"width", "height", "mask_png_b64", "blobs", "coverage"}
    assert d["width"] > 0 and d["height"] > 0 and d["mask_png_b64"]
    assert 15 <= len(d["blobs"]) <= 30
    assert all(len(b) == 4 and all(isinstance(v, int) for v in b) for b in d["blobs"])


def test_a_page_is_analysed_once_and_each_page_on_its_own(client, page_bytes):
    for _ in range(3):
        assert client.get(URL.format(sid="abc", page=0)).status_code == 200
    assert page_bytes == [("abc", 0)]
    assert client.get(URL.format(sid="abc", page=1)).status_code == 200
    assert page_bytes == [("abc", 0), ("abc", 1)]


def test_an_unknown_or_expired_session_is_404(client, page_bytes):
    r = client.get(URL.format(sid="gone", page=0))
    assert r.status_code == 404 and "re-upload" in r.json()["detail"]


def test_a_page_that_is_not_an_image_is_422(client, page_bytes):
    r = client.get(URL.format(sid="corrupt", page=0))
    assert r.status_code == 422


def test_login_is_required():
    from app.main import app
    assert TestClient(app).get(URL.format(sid="abc", page=0)).status_code in (401, 403)
