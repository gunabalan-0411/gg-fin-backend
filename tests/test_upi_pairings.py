"""
UPI pairings made on the OCR page (saved at Submit) and the "Use existing UPI ids" move for a re-opened account.
Both work in the EXISTING tbl_upi_vpa_mappings table — no new table.
"""
from datetime import date

from sqlmodel import select

from app.models.upi import UpiTransaction, UpiVpaMapping
from tests.helpers import add_customer, set_closed


def add_txn(session, ref, vpa="ravi@upi", name="RAVI", amount=500, mapped=None, mtype=None):
    t = UpiTransaction(upi_ref_no=ref, amount=amount, sender_vpa=vpa, sender_name=name, transaction_date=date(2026, 10, 17),
                       mapped_customer_id=mapped, mapped_customer_type=mtype)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def add_vpa(session, vpa, cid, ctype="edi", name="X"):
    m = UpiVpaMapping(upi_vpa=vpa, customer_id=cid, customer_type=ctype, customer_name=name)
    session.add(m)
    session.commit()
    return m


def mappings(session):
    session.expire_all()
    return sorted((m.upi_vpa, m.customer_id, m.customer_type, m.customer_name) for m in session.exec(select(UpiVpaMapping)).all())


def txn(session, txn_id):
    session.expire_all()
    t = session.get(UpiTransaction, txn_id)
    return t.mapped_customer_id, t.mapped_customer_type


def pair(client, *pairings):
    return client.post("/api/upi/vpa-mappings/apply-pairings", json={"pairings": list(pairings)})


def p(txn_id, cid=1, ctype="edi", name="Selvam", replace=False):
    return {"txn_id": txn_id, "customer_id": cid, "customer_type": ctype, "customer_name": name, "replace": replace}


# ── apply-pairings ────────────────────────────────────────────────────────────

def test_new_upi_id_is_mapped_to_the_customer_and_the_transaction_is_marked(client, session):     # T2-15
    t = add_txn(session, "r1", vpa="Ravi@UPI")
    r = pair(client, p(t.id))
    assert r.status_code == 200, r.text
    assert (r.json()["paired"], r.json()["created"], r.json()["skipped"]) == (1, 1, [])
    assert mappings(session) == [("ravi@upi", 1, "edi", "Selvam")]                  # stored lower-case, like the UPI page does
    assert txn(session, t.id) == (1, "edi")


def test_only_unmapped_history_of_the_same_upi_id_follows(client, session):                       # T2-16, CP-05
    paired = add_txn(session, "r1")
    other_unmapped = add_txn(session, "r2", amount=100)
    history = add_txn(session, "r3", amount=200, mapped=9, mtype="edi")                           # belongs to another account's period
    other_vpa = add_txn(session, "r4", vpa="someone@else")
    pair(client, p(paired.id))
    assert txn(session, paired.id) == (1, "edi")
    assert txn(session, other_unmapped.id) == (1, "edi")
    assert txn(session, history.id) == (9, "edi")                                                # history untouched
    assert txn(session, other_vpa.id) == (None, None)


def test_pairing_twice_is_harmless(client, session):                                              # T2-16
    t = add_txn(session, "r1")
    pair(client, p(t.id))
    again = pair(client, p(t.id)).json()
    assert (again["created"], again["unchanged"], again["paired"]) == (0, 1, 1)
    assert len(mappings(session)) == 1


def test_upi_id_belonging_to_someone_else_is_skipped_until_the_user_confirms(client, session):    # T2-15
    t = add_txn(session, "r1")
    old_history = add_txn(session, "r2", mapped=2, mtype="edi")
    add_vpa(session, "ravi@upi", cid=2, name="Old Owner")

    r = pair(client, p(t.id)).json()
    assert r["paired"] == 0 and r["skipped"] == [{"txn_id": t.id, "reason": "vpa_mapped_to_other",
                                                  "mapped_customer_id": 2, "mapped_customer_name": "Old Owner"}]
    assert mappings(session) == [("ravi@upi", 2, "edi", "Old Owner")] and txn(session, t.id) == (None, None)

    r = pair(client, p(t.id, replace=True)).json()
    assert (r["paired"], r["replaced"], r["skipped"]) == (1, 1, [])
    assert mappings(session) == [("ravi@upi", 1, "edi", "Selvam")] and txn(session, t.id) == (1, "edi")
    assert txn(session, old_history.id) == (2, "edi")                                            # the old owner's history stays with the old owner


def test_a_transaction_without_an_upi_id_is_only_marked(client, session):                         # T2-16
    t = add_txn(session, "r1", vpa=None, name="CASH DEPOSIT")
    r = pair(client, p(t.id)).json()
    assert (r["paired"], r["created"]) == (1, 0)
    assert mappings(session) == [] and txn(session, t.id) == (1, "edi")


def test_unknown_transaction_and_bad_type_are_reported_not_fatal(client, session):                # T2-15
    good = add_txn(session, "r1")
    r = pair(client, p(good.id), p(99999), p(good.id, ctype="loan")).json()
    assert r["paired"] == 1
    assert sorted(s["reason"] for s in r["skipped"]) == ["bad_customer_type", "txn_not_found"]


def test_the_same_upi_id_can_belong_to_an_edi_and_an_iop_customer(client, session):               # product isolation
    t = add_txn(session, "r1")
    add_vpa(session, "ravi@upi", cid=7, ctype="iop", name="Iop Ravi")
    r = pair(client, p(t.id, cid=1, ctype="edi")).json()
    assert r["created"] == 1
    assert mappings(session) == [("ravi@upi", 1, "edi", "Selvam"), ("ravi@upi", 7, "iop", "Iop Ravi")]


def test_two_pairings_of_one_upi_id_in_one_batch_do_not_both_win(client, session):                # T2-15
    a, b = add_txn(session, "r1"), add_txn(session, "r2", amount=300)
    r = pair(client, p(a.id, cid=1), p(b.id, cid=2, name="Kannan")).json()
    assert r["paired"] == 1 and r["skipped"][0]["reason"] == "vpa_mapped_to_other"
    assert mappings(session) == [("ravi@upi", 1, "edi", "Selvam")]


def test_vpa_mappings_list_filter(client, session):                                               # T2-19
    add_vpa(session, "a@upi", 1, "edi")
    add_vpa(session, "b@upi", 1, "edi")
    add_vpa(session, "c@upi", 2, "edi")
    add_vpa(session, "d@upi", 1, "iop")

    def ids(**params):
        return sorted(m["upi_vpa"] for m in client.get("/api/upi/vpa-mappings", params=params).json()["data"])
    assert ids() == ["a@upi", "b@upi", "c@upi", "d@upi"]
    assert ids(customer_id=1, customer_type="edi") == ["a@upi", "b@upi"]
    assert ids(customer_id=1, customer_type="EDI") == ["a@upi", "b@upi"]                        # case-insensitive
    assert ids(customer_id=1) == ["a@upi", "b@upi", "d@upi"]
    assert ids(customer_id=3) == []


# ── move (Copy of a closed account -> "Use existing UPI ids", and its undo) ────

def move(client, frm, to, ctype="edi"):
    return client.post("/api/upi/vpa-mappings/move", json={"customer_type": ctype, "from_customer_id": frm, "to_customer_id": to})


def two_accounts(conn, ftype="EDI"):
    add_customer(conn, ftype, 1, "Selvam", closed=True)       # the closed account that is copied
    add_customer(conn, ftype, 2, "Selvam", closed=False)      # the new one


def test_ids_move_from_the_closed_account_to_the_new_one(client, session, conn):                  # T2-18, CP-02
    two_accounts(conn)
    add_vpa(session, "a@upi", 1, name="Selvam")
    add_vpa(session, "b@upi", 1, name="Selvam")
    add_vpa(session, "other@upi", 5, name="Somebody")
    history = add_txn(session, "r1", vpa="a@upi", mapped=1, mtype="edi")

    r = move(client, 1, 2)
    assert r.status_code == 200 and r.json() == {"moved": 2}
    assert mappings(session) == [("a@upi", 2, "edi", "Selvam"), ("b@upi", 2, "edi", "Selvam"), ("other@upi", 5, "edi", "Somebody")]
    assert txn(session, history.id) == (1, "edi")                                                # old transactions stay with the old account


def test_the_mapping_takes_the_new_accounts_name(client, session, conn):                          # T2-18
    add_customer(conn, "EDI", 1, "Selvam K", closed=True)
    add_customer(conn, "EDI", 2, "Selvam Kumar", closed=False)
    add_vpa(session, "a@upi", 1, name="Selvam K")
    move(client, 1, 2)
    assert mappings(session) == [("a@upi", 2, "edi", "Selvam Kumar")]


def test_undo_moves_the_ids_back_to_the_closed_account(client, session, conn):                    # T2-18, CP-04 (server side)
    two_accounts(conn)
    add_vpa(session, "a@upi", 1, name="Selvam")
    move(client, 1, 2)
    r = move(client, 2, 1)                                                                        # open -> closed is the undo direction
    assert r.status_code == 200 and r.json() == {"moved": 1}
    assert mappings(session) == [("a@upi", 1, "edi", "Selvam")]


def test_moving_between_two_open_or_two_closed_accounts_is_refused(client, session, conn):        # T2-18, CP-03
    add_customer(conn, "EDI", 1, "A")
    add_customer(conn, "EDI", 2, "B")
    add_customer(conn, "EDI", 3, "C", closed=True)
    add_customer(conn, "EDI", 4, "D", closed=True)
    add_vpa(session, "a@upi", 1)
    assert move(client, 1, 2).status_code == 400                                                  # open -> open
    assert move(client, 3, 4).status_code == 400                                                  # closed -> closed
    assert move(client, 1, 1).status_code == 400                                                  # same account
    assert mappings(session) == [("a@upi", 1, "edi", "X")]


def test_move_rejects_unknown_customers_and_bad_types(client, conn):                              # T2-18
    two_accounts(conn)
    assert move(client, 1, 99).status_code == 404
    assert move(client, 99, 2).status_code == 404
    assert move(client, 1, 2, ctype="loan").status_code == 400


def test_move_only_touches_the_same_product(client, session, conn):                               # T2-18
    two_accounts(conn, "EDI")
    two_accounts(conn, "IOP")
    add_vpa(session, "edi@upi", 1, "edi")
    add_vpa(session, "iop@upi", 1, "iop")
    move(client, 1, 2, ctype="edi")
    assert mappings(session) == [("edi@upi", 2, "edi", "Selvam"), ("iop@upi", 1, "iop", "X")]            # EDI moved + renamed, IOP untouched


def test_move_with_nothing_to_move_is_fine(client, conn):
    two_accounts(conn)
    assert move(client, 1, 2).json() == {"moved": 0}
