"""
Contract of POST /api/upi/fuzzy-suggest: scores are on a 0-100 scale (UpiPage compares them with 80 / 60).

The OCR page turns them into the 0-1 scale its dropdown uses (`score / 100`), so a change of scale here would silently
break one of the two screens — this test is the tripwire.
"""
from tests.helpers import add_customer


def suggest(client, query):
    r = client.post("/api/upi/fuzzy-suggest", params={"query": query})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_scores_are_on_a_0_to_100_scale(client, conn):
    add_customer(conn, "EDI", 1, "Selvam")
    add_customer(conn, "EDI", 2, "Kannan")
    add_customer(conn, "IOP", 7, "Selvam")

    exact = suggest(client, "Selvam")
    assert exact[0]["customer_name"].lower() == "selvam"
    assert exact[0]["score"] == 100.0                                    # not 1.0

    near = [r for r in suggest(client, "Selvm") if r["customer_id"] == 1 and r["type"] == "edi"][0]
    assert 80 <= near["score"] < 100                                     # a typo of one letter: ~90, not 0.9

    # every score is a plain 0..100 number
    assert all(0 <= r["score"] <= 100 for r in suggest(client, "Kanan"))
    assert max(r["score"] for r in suggest(client, "Kanan")) > 1         # i.e. definitely not the 0..1 scale


def test_response_shape_the_ocr_page_relies_on(client, conn):
    add_customer(conn, "EDI", 1, "Selvam")
    add_customer(conn, "IOP", 7, "Selvam")
    rows = suggest(client, "Selvam")
    for r in rows:
        assert set(r) == {"customer_id", "customer_name", "type", "score", "balance"}
        assert r["type"] in ("edi", "iop")
    assert {r["type"] for r in rows} == {"edi", "iop"}                   # both products, the page filters by type


def test_closed_customers_are_not_suggested(client, conn):
    add_customer(conn, "EDI", 1, "Selvam")
    add_customer(conn, "EDI", 2, "Selvam Closed", closed=True)
    names = [r["customer_name"] for r in suggest(client, "Selvam")]
    assert "Selvam" in names and "Selvam Closed" not in names
