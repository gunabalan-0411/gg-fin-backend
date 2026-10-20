"""
Note Pad API (/api/notes): a few Markdown pages kept in the database.   NP-01 … NP-08 (B5-01 … B5-04, T5-01 … T5-03)
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.services.note_service import MAX_CONTENT_BYTES, MAX_PAGES, MAX_TITLE

URL = "/api/notes"


def make(client, title="Plan", content=""):
    r = client.post(URL, json={"title": title, "content": content})
    assert r.status_code == 201, r.text
    return r.json()


def save(client, page, **fields):
    return client.patch(f"{URL}/{page['id']}", json={"version": page["version"], **fields})


# ── NP-01 create / read ─────────────────────────────────────────────────────────────────────────────────────
def test_empty_to_start_with(client):
    assert client.get(URL).json() == []


def test_create_returns_the_page_with_version_1_and_utc_timestamps(client):
    p = make(client, "Shopping list", "# Milk")
    assert p["title"] == "Shopping list" and p["content"] == "# Milk" and p["version"] == 1 and isinstance(p["id"], int)
    for key in ("created_at", "updated_at"):
        assert datetime.fromisoformat(p[key]).tzinfo is not None             # the browser must not read it as local time
    assert client.get(f"{URL}/{p['id']}").json() == p


@pytest.mark.parametrize("given,expected", [(None, "Untitled"), ("", "Untitled"), ("   ", "Untitled"), ("  a   b \n c ", "a b c")])
def test_titles_are_trimmed_and_blank_means_untitled(client, given, expected):
    r = client.post(URL, json={"title": given})
    assert r.status_code == 201 and r.json()["title"] == expected and r.json()["content"] == ""


def test_the_list_has_no_text_but_the_size_in_bytes_and_is_in_creation_order(client):
    a = make(client, "A", "abc")
    b = make(client, "B", "தமிழ்")                                              # multi-byte: size counts bytes
    rows = client.get(URL).json()
    assert [r["id"] for r in rows] == [a["id"], b["id"]]
    assert all("content" not in r for r in rows)
    assert rows[0]["size"] == 3 and rows[1]["size"] == len("தமிழ்".encode("utf-8"))
    assert {"id", "title", "version", "size", "updated_at"} == set(rows[0])


def test_unicode_markdown_round_trips_exactly(client):
    body = "# தலைப்பு\n\n- [ ] one\n- [x] two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```py\nprint('x')\n```\n\r\n  trailing  "
    p = make(client, "Full", body)
    assert client.get(f"{URL}/{p['id']}").json()["content"] == body


# ── NP-02 update ────────────────────────────────────────────────────────────────────────────────────────────
def test_saving_text_bumps_the_version_and_the_time(client):
    p = make(client, "Plan", "one")
    r = save(client, p, content="two")
    assert r.status_code == 200
    q = r.json()
    assert q["content"] == "two" and q["version"] == 2 and q["title"] == "Plan"
    assert datetime.fromisoformat(q["updated_at"]) >= datetime.fromisoformat(p["updated_at"])
    assert q["created_at"] == p["created_at"]


def test_rename_keeps_the_text(client):
    p = make(client, "Old", "keep me")
    q = save(client, p, title="  New   name ").json()
    assert q["title"] == "New name" and q["content"] == "keep me" and q["version"] == 2


def test_rename_and_text_together_are_one_version(client):
    p = make(client, "Old", "x")
    q = save(client, p, title="New", content="y").json()
    assert (q["title"], q["content"], q["version"]) == ("New", "y", 2)


def test_a_save_that_changes_nothing_does_not_make_a_new_version(client):
    p = make(client, "Same", "text")
    q = save(client, p, title="Same", content="text").json()
    assert q["version"] == 1 and q["updated_at"] == p["updated_at"]
    assert save(client, p).json()["version"] == 1                              # a bare version-only request too


def test_saving_empty_text_is_allowed(client):
    p = make(client, "Clear", "something")
    assert save(client, p, content="").json()["content"] == ""


# ── NP-03 a stale save never overwrites ─────────────────────────────────────────────────────────────────────
def test_a_save_based_on_an_old_version_is_refused_with_the_current_page(client):
    p = make(client, "Shared", "v1")
    other = save(client, p, content="saved on the phone").json()                # someone saves first
    r = save(client, p, content="saved on the laptop")                         # still based on version 1
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "stale" and d["current"]["content"] == "saved on the phone" and d["current"]["version"] == other["version"]
    assert client.get(f"{URL}/{p['id']}").json()["content"] == "saved on the phone"      # nothing was overwritten


def test_after_a_conflict_a_save_on_the_current_version_goes_through(client):
    p = make(client, "Shared", "v1")
    save(client, p, content="theirs")
    current = save(client, p, content="mine").json()["detail"]["current"]
    r = client.patch(f"{URL}/{p['id']}", json={"version": current["version"], "content": "mine"})
    assert r.status_code == 200 and r.json()["content"] == "mine" and r.json()["version"] == current["version"] + 1


def test_a_stale_rename_is_refused_too(client):
    p = make(client, "Shared", "v1")
    save(client, p, content="theirs")
    assert save(client, p, title="Other").status_code == 409


def test_the_version_is_required(client):
    p = make(client)
    assert client.patch(f"{URL}/{p['id']}", json={"content": "x"}).status_code == 422


# ── NP-04 delete / not found ────────────────────────────────────────────────────────────────────────────────
def test_delete_removes_only_that_page(client):
    a, b = make(client, "A"), make(client, "B")
    assert client.delete(f"{URL}/{a['id']}").status_code == 204
    assert [r["id"] for r in client.get(URL).json()] == [b["id"]]
    assert client.get(f"{URL}/{a['id']}").status_code == 404


def test_missing_pages_answer_404_with_a_code(client):
    for call in (lambda: client.get(f"{URL}/999"), lambda: client.patch(f"{URL}/999", json={"version": 1, "title": "x"}),
                 lambda: client.delete(f"{URL}/999")):
        r = call()
        assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"


# ── NP-05 limits ────────────────────────────────────────────────────────────────────────────────────────────
def test_title_length_limit(client):
    assert client.post(URL, json={"title": "t" * MAX_TITLE}).status_code == 201
    r = client.post(URL, json={"title": "t" * (MAX_TITLE + 1)})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "title_too_long"
    p = make(client)
    r = save(client, p, title="t" * (MAX_TITLE + 1))
    assert r.status_code == 422 and client.get(f"{URL}/{p['id']}").json()["title"] == "Plan"


def test_text_size_limit_is_in_bytes_and_exact(client):
    assert client.post(URL, json={"title": "ok", "content": "a" * MAX_CONTENT_BYTES}).status_code == 201
    r = client.post(URL, json={"title": "big", "content": "a" * (MAX_CONTENT_BYTES + 1)})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_large"
    p = make(client)
    wide = "த" * (MAX_CONTENT_BYTES // 3 + 1)                                    # 3 bytes each: under the limit in characters, over in bytes
    assert len(wide) < MAX_CONTENT_BYTES and len(wide.encode("utf-8")) > MAX_CONTENT_BYTES
    r = save(client, p, content=wide)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_large"
    assert client.get(f"{URL}/{p['id']}").json()["content"] == ""               # the refused save wrote nothing


def test_page_limit_and_room_again_after_a_delete(client):
    made = [make(client, f"P{i}") for i in range(MAX_PAGES)]
    r = client.post(URL, json={"title": "one too many"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "limit_reached" and r.json()["detail"]["limit"] == MAX_PAGES
    assert len(client.get(URL).json()) == MAX_PAGES
    client.delete(f"{URL}/{made[0]['id']}")
    assert client.post(URL, json={"title": "fits now"}).status_code == 201


# ── NP-06 safety ────────────────────────────────────────────────────────────────────────────────────────────
def test_markdown_is_stored_as_text_never_interpreted(client, conn):
    evil = "<script>alert(1)</script> ' ; DROP TABLE tbl_note_page; -- [x](javascript:alert(1))"
    p = make(client, "<b>t</b>", evil)
    assert client.get(f"{URL}/{p['id']}").json()["content"] == evil
    assert conn.execute(text("SELECT count(*) FROM tbl_note_page")).scalar() == 1


def test_login_is_required():
    from app.main import app
    anon = TestClient(app)
    for r in (anon.get(URL), anon.post(URL, json={}), anon.get(f"{URL}/1"), anon.patch(f"{URL}/1", json={"version": 1}), anon.delete(f"{URL}/1")):
        assert r.status_code in (401, 403)
