"""
Home TV: one-time pairing, a read-only device token, and the customer search / detail the TV shows. TV-01 ... TV-40 ... TV-56.

The `client` fixture bypasses login; the TV endpoints are tested with a plain TestClient (no override) so the real
X-TV-Token check runs.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import tv as tv_api
from app.api.deps import get_current_user
from app.main import app
from app.models.user import User
from tests.helpers import add_customer

API = "/api/tv"


@pytest.fixture(autouse=True)
def _fresh_limits():
    tv_api.reset_rate_limits()
    yield
    tv_api.reset_rate_limits()
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def tv():
    """A client with NO login override: what the TV (or anybody on the internet) is."""
    app.dependency_overrides.pop(get_current_user, None)
    return TestClient(app)


def login_as(uid: int):
    app.dependency_overrides[get_current_user] = lambda: User(id=uid, username=f"user{uid}", hashed_password="x")


def pair(tv, user_id: int = 1, name: str | None = "Living room TV") -> str:
    """Run the whole pairing and return the device token."""
    s = tv.post(f"{API}/pair/start").json()
    login_as(user_id)
    r = tv.post(f"{API}/pair/approve", json={"code": s["code"], "name": name})
    assert r.status_code == 200, r.text
    app.dependency_overrides.pop(get_current_user, None)
    p = tv.post(f"{API}/pair/poll", json={"poll_id": s["poll_id"]}).json()
    assert p["status"] == "approved", p
    return p["token"]


def H(token: str) -> dict:
    return {"X-TV-Token": token}


# ── Pairing ──────────────────────────────────────────────────────────────────────────────────────────────

def test_tv01_start_gives_a_six_digit_code_and_a_secret(tv):
    r = tv.post(f"{API}/pair/start")
    assert r.status_code == 200
    b = r.json()
    assert len(b["code"]) == 6 and b["code"].isdigit()
    assert len(b["poll_id"]) >= 20 and b["expires_in"] == 600


def test_tv02_pending_until_approved(tv):
    s = tv.post(f"{API}/pair/start").json()
    assert tv.post(f"{API}/pair/poll", json={"poll_id": s["poll_id"]}).json() == {"status": "pending"}


def test_tv03_approve_then_the_tv_gets_a_token_once(tv):
    s = tv.post(f"{API}/pair/start").json()
    login_as(1)
    r = tv.post(f"{API}/pair/approve", json={"code": s["code"], "name": "Hall TV"})
    assert r.status_code == 200 and r.json() == {"ok": True, "name": "Hall TV"}
    app.dependency_overrides.pop(get_current_user, None)
    first = tv.post(f"{API}/pair/poll", json={"poll_id": s["poll_id"]}).json()
    assert first["status"] == "approved" and first["token"].startswith("ggtv_") and first["name"] == "Hall TV"
    again = tv.post(f"{API}/pair/poll", json={"poll_id": s["poll_id"]}).json()
    assert again == {"status": "expired"}                                   # the token is handed over once


def test_tv04_approving_needs_a_login(tv):
    s = tv.post(f"{API}/pair/start").json()
    r = tv.post(f"{API}/pair/approve", json={"code": s["code"]})
    assert r.status_code == 401


def test_tv05_wrong_unknown_or_malformed_code_is_404(tv):
    live = tv.post(f"{API}/pair/start").json()["code"]
    login_as(1)
    other = f"{(int(live) + 1) % 1_000_000:06d}"                                            # a well-formed code nobody was given
    assert tv.post(f"{API}/pair/approve", json={"code": other}).status_code == 404
    for bad in ("12", "abcdef", "1234567", " "):
        assert tv.post(f"{API}/pair/approve", json={"code": bad}).status_code == 404


def test_tv06_a_code_works_once(tv):
    s = tv.post(f"{API}/pair/start").json()
    login_as(1)
    assert tv.post(f"{API}/pair/approve", json={"code": s["code"]}).status_code == 200
    assert tv.post(f"{API}/pair/approve", json={"code": s["code"]}).status_code == 404


def test_tv07_an_expired_code_is_refused(tv, conn):
    s = tv.post(f"{API}/pair/start").json()
    conn.execute(text("UPDATE tbl_tv_pairing SET expires_at = now() - interval '1 minute'"))
    login_as(1)
    assert tv.post(f"{API}/pair/approve", json={"code": s["code"]}).status_code == 404
    assert tv.post(f"{API}/pair/poll", json={"poll_id": s["poll_id"]}).json() == {"status": "expired"}


def test_tv08_unknown_poll_secret_is_just_expired(tv):
    assert tv.post(f"{API}/pair/poll", json={"poll_id": "x" * 30}).json() == {"status": "expired"}


def test_tv09_code_may_be_typed_with_spaces_or_a_dash(tv):
    s = tv.post(f"{API}/pair/start").json()
    login_as(1)
    spaced = f"{s['code'][:3]} {s['code'][3:]}"
    assert tv.post(f"{API}/pair/approve", json={"code": spaced}).status_code == 200


def test_tv10_default_name_when_none_given(tv):
    s = tv.post(f"{API}/pair/start").json()
    login_as(1)
    assert tv.post(f"{API}/pair/approve", json={"code": s["code"]}).json()["name"] == "TV"


def test_tv11_two_waiting_tvs_get_different_codes(tv):
    codes = {tv.post(f"{API}/pair/start").json()["code"] for _ in range(5)}
    assert len(codes) == 5


def test_tv12_only_hashes_are_stored(tv, conn):
    token = pair(tv)
    rows = conn.execute(text("SELECT token_hash FROM tbl_tv_device")).all()
    assert len(rows) == 1 and rows[0][0] != token and token not in rows[0][0] and len(rows[0][0]) == 64
    assert conn.execute(text("SELECT count(*) FROM tbl_tv_pairing WHERE poll_hash LIKE 'ggtv_%'")).scalar() == 0


def test_tv13_start_and_poll_are_rate_limited(tv):
    for _ in range(10):
        assert tv.post(f"{API}/pair/start").status_code == 200
    assert tv.post(f"{API}/pair/start").status_code == 429
    for _ in range(60):
        assert tv.post(f"{API}/pair/poll", json={"poll_id": "y" * 30}).status_code == 200
    assert tv.post(f"{API}/pair/poll", json={"poll_id": "y" * 30}).status_code == 429


# ── Device token: read-only, revocable ──────────────────────────────────────────────────────────────────────

def test_tv14_tv_endpoints_need_the_token(tv):
    for path in (f"{API}/me", f"{API}/search?q=a", f"{API}/customers/edi/1"):
        assert tv.get(path).status_code == 401
        assert tv.get(path, headers=H("ggtv_nope")).status_code == 401
        assert tv.get(path, headers=H("not-even-prefixed")).status_code == 401


def test_tv15_me_returns_the_name(tv):
    token = pair(tv, name="Living room TV")
    assert tv.get(f"{API}/me", headers=H(token)).json() == {"name": "Living room TV"}


def test_tv16_the_token_is_not_a_login(tv):
    token = pair(tv)
    for path in ("/api/customers/edi", "/api/personal/expenses", "/api/debts", "/api/notes"):
        assert tv.get(path, headers=H(token)).status_code == 401
        assert tv.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_tv17_the_tv_cannot_change_anything(tv):
    token = pair(tv)
    assert tv.post(f"{API}/me", headers=H(token)).status_code == 405
    assert tv.delete(f"{API}/customers/edi/1", headers=H(token)).status_code == 405
    assert tv.post("/api/customers/edi", json={}, headers=H(token)).status_code == 401
    assert tv.get(f"{API}/devices", headers=H(token)).status_code == 401     # it cannot list or revoke TVs either


def test_tv18_devices_list_is_mine_only_and_revoke_works(tv):
    t1 = pair(tv, user_id=1, name="One")
    t2 = pair(tv, user_id=2, name="Two")
    login_as(1)
    mine = tv.get(f"{API}/devices").json()
    assert [d["name"] for d in mine] == ["One"] and "token" not in mine[0] and "token_hash" not in mine[0]
    dev_id = mine[0]["id"]
    login_as(2)
    assert tv.delete(f"{API}/devices/{dev_id}").status_code == 404           # someone else's TV
    assert tv.get(f"{API}/me", headers=H(t1)).status_code == 200
    login_as(1)
    assert tv.delete(f"{API}/devices/{dev_id}").status_code == 204
    assert tv.get(f"{API}/me", headers=H(t1)).status_code == 401              # cut off at once
    assert tv.get(f"{API}/me", headers=H(t2)).status_code == 200              # the other one is untouched
    assert tv.get(f"{API}/devices").json() == []
    assert tv.delete(f"{API}/devices/{dev_id}").status_code == 404            # already gone


def test_tv19_listing_and_revoking_need_a_login(tv):
    assert tv.get(f"{API}/devices").status_code == 401
    assert tv.delete(f"{API}/devices/1").status_code == 401


def test_tv20_last_seen_is_recorded(tv, conn):
    token = pair(tv)
    assert conn.execute(text("SELECT last_seen_at FROM tbl_tv_device")).scalar() is None
    tv.get(f"{API}/me", headers=H(token))
    seen = conn.execute(text("SELECT last_seen_at FROM tbl_tv_device")).scalar()
    assert seen is not None and abs((datetime.now(timezone.utc) - seen).total_seconds()) < 60
    login_as(1)
    assert tv.get(f"{API}/devices").json()[0]["last_seen_at"] is not None


# ── Search ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def people(conn):
    add_customer(conn, "EDI", 101, "Selvam K")
    add_customer(conn, "EDI", 102, "Murugan S")
    add_customer(conn, "EDI", 103, "Meena Kumari", closed=True)
    add_customer(conn, "IOP", 201, "Gopal Samy")
    add_customer(conn, "IOP", 202, "Karthikeyan")
    add_customer(conn, "IOP", 101, "Selvi Ammal")                                          # same id as an EDI customer
    conn.execute(text("UPDATE tbl_edi_name_map SET customer_name_ta = 'செல்வம் கே' WHERE customer_id = 101"))
    conn.execute(text("UPDATE tbl_iop_name_map SET customer_name_ta = 'கோபால் சாமி' WHERE customer_id = 201"))
    conn.execute(text("UPDATE tbl_edi_customer SET contact_number = '98765 43210', outstanding_balance = 11800 WHERE customer_id = 101"))
    conn.execute(text("UPDATE tbl_edi_customer SET contact_number = '99440 11223', outstanding_balance = 12400 WHERE customer_id = 102"))
    return conn


def names(tv, token, q):
    r = tv.get(f"{API}/search", params={"q": q}, headers=H(token))
    assert r.status_code == 200, r.text
    return [x["name_en"] for x in r.json()["results"]]


def test_tv21_empty_search_returns_nothing(tv, people):
    token = pair(tv)
    assert tv.get(f"{API}/search", params={"q": "  "}, headers=H(token)).json() == {"results": []}
    assert tv.get(f"{API}/search", headers=H(token)).json() == {"results": []}


def test_tv22_misheard_names_still_find_the_person(tv, people):
    token = pair(tv)
    assert names(tv, token, "selvem")[0] == "Selvam K"
    assert names(tv, token, "murgan")[0] == "Murugan S"
    assert names(tv, token, "kartikeyan")[0] == "Karthikeyan"
    assert names(tv, token, "gopal sami")[0] == "Gopal Samy"


def test_tv23_phonetic_hearing_is_covered(tv, people):
    token = pair(tv)
    assert names(tv, token, "celvam")[0] == "Selvam K"                                      # heard C for S


def test_tv24_tamil_speech_matches_the_tamil_name(tv, people):
    token = pair(tv)
    assert names(tv, token, "கோபால்")[0] == "Gopal Samy"
    assert names(tv, token, "செல்வம்")[0] == "Selvam K"


def test_tv25_closed_customers_are_never_offered(tv, people):
    token = pair(tv)
    assert "Meena Kumari" not in names(tv, token, "meena")
    assert "Meena Kumari" not in names(tv, token, "meena kumari")


def test_tv26_nonsense_finds_nobody(tv, people):
    token = pair(tv)
    assert names(tv, token, "xqzv") == []


def test_tv27_at_most_five_best_first(tv, conn):
    for i in range(8):
        add_customer(conn, "EDI", 300 + i, f"Selvan {i}")
    token = pair(tv)
    r = tv.get(f"{API}/search", params={"q": "selvan"}, headers=H(token)).json()["results"]
    assert len(r) == 5
    scores = [x["score"] for x in r]
    assert scores == sorted(scores, reverse=True) and all(s >= 60 for s in scores)


def test_tv28_phone_number_finds_by_number(tv, people):
    token = pair(tv)
    assert names(tv, token, "99440") == ["Murugan S"]
    assert names(tv, token, "98765 43210") == ["Selvam K"]
    assert names(tv, token, "12") == []                                                      # too short to mean anything


def test_tv29_a_second_alias_also_finds_the_person(tv, people, conn):
    token = pair(tv)
    conn.execute(text("INSERT INTO tbl_customer_alias (finance_type, customer_name, alias_name, active_customer_id) VALUES ('EDI','Murugan S','Murugesh',102)"))
    assert names(tv, token, "murugesh")[0] == "Murugan S"


def test_tv30_result_shape_and_same_id_in_two_products(tv, people):
    token = pair(tv)
    r = tv.get(f"{API}/search", params={"q": "selvam"}, headers=H(token)).json()["results"]
    top = r[0]
    assert set(top) == {"product", "customer_id", "name_en", "name_ta", "outstanding", "score"}
    assert (top["product"], top["customer_id"], top["name_ta"], top["outstanding"]) == ("edi", 101, "செல்வம் கே", 11800.0)
    seen = {(x["product"], x["customer_id"]) for x in tv.get(f"{API}/search", params={"q": "sel"}, headers=H(token)).json()["results"]}
    assert ("edi", 101) in seen and ("iop", 101) in seen                                     # EDI 101 and IOP 101 are different people


# ── Customer detail ──────────────────────────────────────────────────────────────────────────────────────

def txn(conn, product, cid, day, amount, mode="CASH", status="PAID", principal=0):
    if product.lower() == "edi":
        conn.execute(text("INSERT INTO tbl_edi_transactions (customer_id, collection_date, amount, payment_mode, payment_status) VALUES (:c,:d,:a,:m,:s)"),
                     {"c": cid, "d": day, "a": amount, "m": mode, "s": status})
    else:
        conn.execute(text("INSERT INTO tbl_iop_transactions (customer_id, collection_date, amount, payment_mode, payment_status, principal_amount) VALUES (:c,:d,:a,:m,:s,:p)"),
                     {"c": cid, "d": day, "a": amount, "m": mode, "s": status, "p": principal})


@pytest.fixture()
def loans(conn):
    add_customer(conn, "EDI", 1, "Selvam K")
    conn.execute(text(
        "UPDATE tbl_edi_customer SET customer_name_ta='செல்வம் கே', contact_number='98765 43210', customer_address='Gandhipuram', loan_start_date='2026-08-14',"
        " loan_amount=25000, disbursed_amount=22500, interest=2500, outstanding_balance=11800, proof_aadhaar='123412341234', remarks='secret note' WHERE customer_id=1"))
    for i in range(5):
        txn(conn, "EDI", 1, date(2026, 11, 9) - timedelta(days=i), 250, "ONLINE" if i % 2 == 0 else "CASH", "UNPAID" if i == 2 else "PAID")
    add_customer(conn, "IOP", 5, "Gopal Samy")
    conn.execute(text("UPDATE tbl_iop_customer SET loan_amount=150000, disbursed_amount=150000, interest=3000, principal_paid=25000, outstanding_balance=125000, loan_start_date='2026-02-12' WHERE customer_id=5"))
    txn(conn, "IOP", 5, date(2026, 11, 5), 3000, "ONLINE", "PAID")
    txn(conn, "IOP", 5, date(2026, 10, 20), 3000, "CASH", "PAID", principal=12500)
    return conn


def detail(tv, token, product, cid):
    return tv.get(f"{API}/customers/{product}/{cid}", headers=H(token))


def test_tv31_edi_detail_has_everything_the_tv_shows(tv, loans):
    token = pair(tv)
    d = detail(tv, token, "edi", 1).json()
    assert (d["name_en"], d["name_ta"], d["phone"], d["address"], d["loan_start_date"]) == ("Selvam K", "செல்வம் கே", "98765 43210", "Gandhipuram", "2026-08-14")
    assert (d["loan_amount"], d["disbursed_amount"], d["interest"], d["outstanding"]) == (25000.0, 22500.0, 2500.0, 11800.0)
    assert "principal_paid" not in d


def test_tv32_edi_payments_all_newest_first_with_gg_and_paid_flags(tv, loans):
    token = pair(tv)
    p = detail(tv, token, "edi", 1).json()["payments"]
    assert [x["date"] for x in p] == ["2026-11-09", "2026-11-08", "2026-11-07", "2026-11-06", "2026-11-05"]
    assert [x["gg"] for x in p] == [True, False, True, False, True]
    assert [x["paid"] for x in p] == [True, True, False, True, True]                         # 07 Nov is GG + UNPAID: the red X
    assert all(x["amount"] == 250.0 and x["principal"] == 0.0 for x in p)


def test_tv33_total_paid_counts_only_paid_rows(tv, loans):
    token = pair(tv)
    assert detail(tv, token, "edi", 1).json()["total_paid"] == 1000.0                        # 5 x 250 minus the unpaid one


def test_tv34_iop_detail_adds_principal(tv, loans):
    token = pair(tv)
    d = detail(tv, token, "iop", 5).json()
    assert (d["loan_amount"], d["interest"], d["principal_paid"], d["outstanding"]) == (150000.0, 3000.0, 25000.0, 125000.0)
    assert "disbursed_amount" not in d
    assert [(x["date"], x["amount"], x["principal"], x["gg"]) for x in d["payments"]] == [("2026-11-05", 3000.0, 0.0, True), ("2026-10-20", 15500.0, 12500.0, False)]
    assert d["total_paid"] == 6000.0 + 25000.0                                               # interest received + principal already returned


def test_tv35_aadhaar_remarks_segment_are_never_sent(tv, loans):
    token = pair(tv)
    body = detail(tv, token, "edi", 1).text
    assert "123412341234" not in body and "secret note" not in body and "proof_aadhaar" not in body and "remarks" not in body and "segment" not in body


def test_tv36_closed_missing_or_unknown_product_is_404(tv, loans, conn):
    token = pair(tv)
    assert detail(tv, token, "edi", 999).status_code == 404
    assert detail(tv, token, "xyz", 1).status_code == 404
    conn.execute(text("UPDATE tbl_edi_customer SET is_closed = true WHERE customer_id = 1"))
    assert detail(tv, token, "edi", 1).status_code == 404


def test_tv37_a_customer_with_no_payments_has_an_empty_list(tv, conn):
    add_customer(conn, "EDI", 9, "New Person")
    token = pair(tv)
    d = detail(tv, token, "edi", 9).json()
    assert d["payments"] == [] and d["total_paid"] == 0.0 and d["loan_amount"] == 0.0


def test_tv38_product_in_the_url_is_case_insensitive(tv, loans):
    token = pair(tv)
    assert detail(tv, token, "EDI", 1).status_code == 200


def test_tv39_every_payment_is_returned_not_a_page(tv, conn):
    add_customer(conn, "EDI", 7, "Long History")
    for i in range(120):
        txn(conn, "EDI", 7, date(2026, 1, 1) + timedelta(days=i), 100)
    token = pair(tv)
    assert len(detail(tv, token, "edi", 7).json()["payments"]) == 120


def test_tv40_a_revoked_token_cannot_search_or_view(tv, people):
    token = pair(tv)
    login_as(1)
    dev = tv.get(f"{API}/devices").json()[0]["id"]
    tv.delete(f"{API}/devices/{dev}")
    app.dependency_overrides.pop(get_current_user, None)
    assert tv.get(f"{API}/search", params={"q": "selvam"}, headers=H(token)).status_code == 401
    assert detail(tv, token, "edi", 101).status_code == 401


# ── EL / தவணை switch: search limited to one product ─────────────────────────────────────────────────────

def test_tv41_product_limits_the_search_to_one_product(tv, people):
    token = pair(tv)
    r = tv.get(f"{API}/search", params={"q": "sel", "product": "iop"}, headers=H(token)).json()["results"]
    assert [x["name_en"] for x in r] == ["Selvi Ammal"] and {x["product"] for x in r} == {"iop"}
    r = tv.get(f"{API}/search", params={"q": "sel", "product": "edi"}, headers=H(token)).json()["results"]
    assert [x["name_en"] for x in r] == ["Selvam K"] and {x["product"] for x in r} == {"edi"}


def test_tv42_no_product_still_searches_both(tv, people):
    token = pair(tv)
    r = tv.get(f"{API}/search", params={"q": "sel"}, headers=H(token)).json()["results"]
    assert {x["product"] for x in r} == {"edi", "iop"}


def test_tv43_an_unknown_product_is_rejected(tv, people):
    token = pair(tv)
    assert tv.get(f"{API}/search", params={"q": "sel", "product": "xyz"}, headers=H(token)).status_code == 422


def test_tv44_phone_search_respects_the_product_too(tv, people, conn):
    conn.execute(text("UPDATE tbl_iop_customer SET contact_number = '99440 99999' WHERE customer_id = 201"))
    token = pair(tv)
    assert names(tv, token, "99440") == ["Murugan S", "Gopal Samy"] or set(names(tv, token, "99440")) == {"Murugan S", "Gopal Samy"}
    r = tv.get(f"{API}/search", params={"q": "99440", "product": "iop"}, headers=H(token)).json()["results"]
    assert [x["name_en"] for x in r] == ["Gopal Samy"]


# ── Collections page (வசூல்): mirrors the dashboard's Reminder report (EL) and Defaulter report (EDI) ─────────

TODAY = date(2026, 11, 11)          # a Wednesday


@pytest.fixture()
def coll(conn, monkeypatch):
    """IOP customers whose payment days are start_day, start_day + freq, ... (start 2026-01-05 -> 5, 15, 25 for a 10-day customer)."""
    from app.services import tv_service
    monkeypatch.setattr(tv_service, "today_ist", lambda: TODAY)
    return conn


def iop(conn, cid, name, freq, interest, start, closed=False, ignore=False, loan=100000, phone="98765 43210"):
    add_customer(conn, "IOP", cid, name, closed=closed)
    conn.execute(text("UPDATE tbl_iop_customer SET interest_payment_frequency=:f, interest=:i, loan_amount=:l, contact_number=:p, loan_start_date=:s, ignore=:ig WHERE customer_id=:c"),
                 {"f": freq, "i": interest, "l": loan, "p": phone, "s": start, "ig": ignore, "c": cid})


def get_coll(tv, token):
    r = tv.get(f"{API}/collections", headers=H(token))
    assert r.status_code == 200, r.text
    return r.json()


def ids(rows):
    return [r["customer_id"] for r in rows]


def test_tv45_collections_need_the_token(tv, coll):
    assert tv.get(f"{API}/collections").status_code == 401
    assert tv.get(f"{API}/collections", headers=H("ggtv_nope")).status_code == 401


def test_tv46_el_follows_the_reminder_report_day_by_day(tv, coll):
    # 10-day customers: due on the 1st, 11th, 21st (start 01) / 2nd, 12th, 22nd ... ; today is the 11th
    iop(coll, 1, "Due Today", 10, 500, "2026-01-01")        # 1, 11, 21, 31 -> today
    iop(coll, 2, "Due Yesterday", 10, 500, "2026-01-10")    # 10, 20, 30 -> yesterday (10th)
    iop(coll, 3, "Due Tomorrow", 10, 500, "2026-01-02")     # 2, 12, 22 -> tomorrow (12th)
    iop(coll, 4, "Not Near", 10, 500, "2026-01-05")         # 5, 15, 25 -> none of the three days
    d = get_coll(tv, pair(tv))
    assert d["as_of"] == "2026-11-11"
    assert (d["el"]["yesterday"]["date"], d["el"]["today"]["date"], d["el"]["tomorrow"]["date"]) == ("2026-11-10", "2026-11-11", "2026-11-12")
    assert ids(d["el"]["yesterday"]["ten_days"]) == [2]
    assert ids(d["el"]["today"]["ten_days"]) == [1]
    assert ids(d["el"]["tomorrow"]["ten_days"]) == [3]


def test_tv47_the_amount_is_the_interest_of_one_collection_as_the_report_shows_it(tv, coll):
    iop(coll, 1, "Ten", 10, 500, "2026-01-01", loan=10000)
    iop(coll, 2, "Month", 30, 9000, "2026-01-11", loan=300000)             # 11, 41(skipped) -> due on the 11th
    row = get_coll(tv, pair(tv))["el"]["today"]
    ten = next(r for r in row["ten_days"] if r["customer_id"] == 1)
    month = next(r for r in row["monthly"] if r["customer_id"] == 2)
    assert ten["due"] == 500 and ten["loan_amount"] == 10000 and ten["phone"] == "98765 43210" and ten["frequency"] == 10
    assert month["due"] == 9000 and month["frequency"] == 30                 # not divided, not multiplied: the `interest` of the customer


def test_tv48_every_30_days_or_more_goes_in_the_monthly_card_shorter_in_the_upper_one(tv, coll):
    iop(coll, 1, "Ten", 10, 500, "2026-01-01")
    iop(coll, 2, "Thirty", 30, 3000, "2026-01-11")
    iop(coll, 3, "Fifteen", 15, 700, "2026-01-11")                         # 11, 26: due today, shorter than a month -> upper card
    t = get_coll(tv, pair(tv))["el"]["today"]
    assert ids(t["ten_days"]) == [1, 3] and ids(t["monthly"]) == [2]


def test_tv49_closed_ignored_and_dateless_customers_are_left_out_like_the_report(tv, coll):
    iop(coll, 1, "Open", 10, 500, "2026-01-01")
    iop(coll, 2, "Closed", 10, 500, "2026-01-01", closed=True)
    iop(coll, 3, "Ignored", 10, 500, "2026-01-01", ignore=True)
    iop(coll, 4, "No Date", 10, 500, None)
    t = get_coll(tv, pair(tv))["el"]["today"]
    assert ids(t["ten_days"]) == [1]


def test_tv50_paid_this_month_counts_only_paid_interest_of_this_month(tv, coll):
    iop(coll, 1, "Open", 10, 500, "2026-01-01")
    txn(coll, "IOP", 1, date(2026, 11, 1), 500)
    txn(coll, "IOP", 1, date(2026, 11, 10), 500, status="UNPAID")           # not received
    txn(coll, "IOP", 1, date(2026, 10, 31), 500)                            # last month
    txn(coll, "IOP", 1, date(2026, 11, 5), 300, principal=20000)            # interest 300 + principal: only the interest counts
    txn(coll, "IOP", 1, date(2026, 11, 20), 700)                            # after "today"
    assert get_coll(tv, pair(tv))["el"]["today"]["ten_days"][0]["paid_month"] == 800


def test_tv51_names_tamil_with_english_fallback(tv, coll):
    iop(coll, 1, "Ten Day A", 10, 500, "2026-01-01")
    iop(coll, 2, "Ten Day B", 10, 500, "2026-01-01")
    coll.execute(text("UPDATE tbl_iop_name_map SET customer_name_ta='பத்து' WHERE customer_id=1"))
    rows = get_coll(tv, pair(tv))["el"]["today"]["ten_days"]
    assert (rows[0]["name_ta"], rows[0]["name_en"]) == ("பத்து", "Ten Day A")
    assert (rows[1]["name_ta"], rows[1]["name_en"]) == ("", "Ten Day B")


def edi(conn, cid, name, start, closed=False, ignore=False, loan=25000, balance=11800):
    add_customer(conn, "EDI", cid, name, closed=closed)
    conn.execute(text("UPDATE tbl_edi_customer SET loan_start_date=:s, loan_amount=:l, outstanding_balance=:b, contact_number='99440 11223', proof_aadhaar='123412341234', remarks='secret', ignore=:ig WHERE customer_id=:c"),
                 {"s": start, "l": loan, "b": balance, "ig": ignore, "c": cid})


def test_tv52_thavanai_is_days_since_the_last_payment_95_and_over_like_the_defaulter_report(tv, coll):
    edi(coll, 10, "Paid long ago", TODAY - timedelta(days=300))
    txn(coll, "EDI", 10, TODAY - timedelta(days=95), 250)                    # exactly 95 days ago: listed
    edi(coll, 11, "Paid recently", TODAY - timedelta(days=300))
    txn(coll, "EDI", 11, TODAY - timedelta(days=94), 250)                    # 94: not listed
    edi(coll, 12, "Old payment but a recent one too", TODAY - timedelta(days=300))
    txn(coll, "EDI", 12, TODAY - timedelta(days=200), 250)
    txn(coll, "EDI", 12, TODAY - timedelta(days=3), 250)                     # the latest PAID one counts: 3 days: not listed
    edi(coll, 13, "Never paid, started long ago", TODAY - timedelta(days=140))        # no payment at all: counted from the start date
    edi(coll, 14, "Never paid, started lately", TODAY - timedelta(days=50))
    edi(coll, 15, "Only an UNPAID row", TODAY - timedelta(days=130))
    txn(coll, "EDI", 15, TODAY - timedelta(days=2), 250, status="UNPAID")    # an unpaid row is not a payment: 130 days
    d = get_coll(tv, pair(tv))["edi_long"]
    assert ids(d) == [13, 15, 10]                                            # 140, 130, 95 days: longest first
    by = {r["customer_id"]: r for r in d}
    assert by[10]["days"] == 95 and by[10]["last_payment_date"] == (TODAY - timedelta(days=95)).isoformat()
    assert by[13]["days"] == 140 and by[13]["last_payment_date"] is None
    assert by[10]["loan_amount"] == 25000 and by[10]["balance"] == 11800 and by[10]["phone"] == "99440 11223"


def test_tv53_thavanai_leaves_out_closed_ignored_customers(tv, coll):
    edi(coll, 20, "Closed", TODAY - timedelta(days=200), closed=True)
    edi(coll, 21, "Ignored", TODAY - timedelta(days=200), ignore=True)
    edi(coll, 22, "Listed", TODAY - timedelta(days=200))
    assert ids(get_coll(tv, pair(tv))["edi_long"]) == [22]


def test_tv54_the_tv_list_equals_the_dashboard_defaulter_report(tv, coll, client, monkeypatch):
    """The same customers, in the same order, with the same days as Dashboard -> Defaulters (the report runs on the database's own date)."""
    from app.services import tv_service
    db_today = coll.execute(text("SELECT CURRENT_DATE")).scalar()
    monkeypatch.setattr(tv_service, "today_ist", lambda: db_today)
    for cid, ago in [(30, 300), (31, 120), (32, 96), (33, 60)]:
        edi(coll, cid, f"C{cid}", db_today - timedelta(days=ago))
    edi(coll, 34, "With a payment", db_today - timedelta(days=400))
    txn(coll, "EDI", 34, db_today - timedelta(days=110), 250)
    report = client.get("/api/dashboard/edi-defaulters").json()
    on_tv = get_coll(tv, pair(tv))["edi_long"]
    assert [r["customer_id"] for r in on_tv] == [30, 31, 34, 32]
    assert [(r["customer_id"], r["days_overdue"]) for r in report] == [(r["customer_id"], r["days"]) for r in on_tv]


def test_tv55_nothing_private_leaves_and_it_is_read_only(tv, coll):
    edi(coll, 40, "X", TODAY - timedelta(days=200))
    token = pair(tv)
    body = tv.get(f"{API}/collections", headers=H(token)).text
    assert "123412341234" not in body and "secret" not in body and "aadhaar" not in body.lower() and "remarks" not in body.lower()
    assert tv.post(f"{API}/collections", headers=H(token), json={}).status_code == 405


def test_tv56_an_empty_database_gives_empty_lists(tv, coll):
    d = get_coll(tv, pair(tv))
    assert d["edi_long"] == [] and all(d["el"][k]["ten_days"] == [] and d["el"][k]["monthly"] == [] for k in ("yesterday", "today", "tomorrow"))
