"""
"Unlink" on a mapped UPI transaction (OCR page): the UPI id stops pointing at the customer it is linked to.
Deletes the saved UPI id -> customer mapping, clears the link on that transaction and on the id's other payments still
linked to the same customer. Payments linked to someone else, other UPI ids and other products are left alone.
"""
from sqlmodel import select

from app.models.upi import UpiTransaction, UpiVpaMapping
from tests.test_upi_pairings import add_txn, add_vpa, mappings, txn


def unlink(client, txn_id):
    return client.post("/api/upi/vpa-mappings/unlink", json={"txn_id": txn_id})


def test_unlink_removes_the_saved_mapping_and_the_link(client, session):                          # U-01
    t = add_txn(session, "r1", vpa="parthi@upi", name="PARTHIBAN", mapped=3, mtype="iop")
    add_vpa(session, "parthi@upi", 3, "iop", "sathiskumar")

    r = unlink(client, t.id)
    assert r.status_code == 200, r.text
    assert r.json() == {"removed_mappings": 1, "cleared": 1, "upi_vpa": "parthi@upi"}
    assert mappings(session) == []
    assert txn(session, t.id) == (None, None)


def test_the_ids_other_payments_linked_to_the_same_customer_lose_the_link_too(client, session):   # U-02
    a = add_txn(session, "r1", vpa="Parthi@UPI", mapped=3, mtype="iop")
    b = add_txn(session, "r2", vpa="parthi@upi", amount=100, mapped=3, mtype="iop")
    add_vpa(session, "parthi@upi", 3, "iop", "sathiskumar")

    r = unlink(client, a.id).json()
    assert (r["removed_mappings"], r["cleared"]) == (1, 2)
    assert txn(session, a.id) == (None, None) and txn(session, b.id) == (None, None)


def test_payments_linked_to_another_customer_other_ids_and_other_products_are_untouched(client, session):   # U-03
    target = add_txn(session, "r1", vpa="parthi@upi", mapped=3, mtype="iop")
    other_customer = add_txn(session, "r2", vpa="parthi@upi", mapped=9, mtype="iop")            # an earlier owner's history
    other_product = add_txn(session, "r3", vpa="parthi@upi", mapped=3, mtype="edi")             # same id number, other product
    other_vpa = add_txn(session, "r4", vpa="someone@else", mapped=3, mtype="iop")
    unlinked_yet = add_txn(session, "r5", vpa="parthi@upi")
    add_vpa(session, "parthi@upi", 3, "iop", "sathiskumar")
    add_vpa(session, "parthi@upi", 3, "edi", "Sathis EDI")
    add_vpa(session, "someone@else", 3, "iop", "sathiskumar")

    unlink(client, target.id)
    assert txn(session, target.id) == (None, None)
    assert txn(session, other_customer.id) == (9, "iop")
    assert txn(session, other_product.id) == (3, "edi")
    assert txn(session, other_vpa.id) == (3, "iop")
    assert txn(session, unlinked_yet.id) == (None, None)
    assert mappings(session) == [("parthi@upi", 3, "edi", "Sathis EDI"), ("someone@else", 3, "iop", "sathiskumar")]


def test_the_mapping_of_a_different_customer_is_kept(client, session):                              # U-04
    t = add_txn(session, "r1", vpa="parthi@upi", mapped=3, mtype="iop")                         # linked one by one to #3 ...
    add_vpa(session, "parthi@upi", 4, "iop", "Somebody Else")                                   # ... but the id is saved for #4
    r = unlink(client, t.id).json()
    assert (r["removed_mappings"], r["cleared"]) == (0, 1)
    assert mappings(session) == [("parthi@upi", 4, "iop", "Somebody Else")]
    assert txn(session, t.id) == (None, None)


def test_a_transaction_without_an_upi_id_only_loses_its_own_link(client, session):                  # U-05
    t = add_txn(session, "r1", vpa=None, name="CASH DEPOSIT", mapped=3, mtype="iop")
    keep = add_txn(session, "r2", vpa=None, name="CASH DEPOSIT", mapped=3, mtype="iop")
    r = unlink(client, t.id).json()
    assert (r["removed_mappings"], r["cleared"], r["upi_vpa"]) == (0, 1, None)
    assert txn(session, t.id) == (None, None) and txn(session, keep.id) == (3, "iop")


def test_an_unlinked_transaction_and_an_unknown_one_are_refused(client, session):                   # U-06
    t = add_txn(session, "r1")
    assert unlink(client, t.id).status_code == 400
    assert unlink(client, 99999).status_code == 404


def test_unlinking_twice_is_refused_the_second_time_and_changes_nothing(client, session):          # U-07
    t = add_txn(session, "r1", vpa="parthi@upi", mapped=3, mtype="iop")
    add_vpa(session, "parthi@upi", 3, "iop", "sathiskumar")
    assert unlink(client, t.id).status_code == 200
    assert unlink(client, t.id).status_code == 400
    assert mappings(session) == []


def test_the_id_can_be_linked_again_afterwards(client, session):                                    # U-08
    t = add_txn(session, "r1", vpa="parthi@upi", mapped=3, mtype="iop")
    add_vpa(session, "parthi@upi", 3, "iop", "sathiskumar")
    unlink(client, t.id)
    r = client.post("/api/upi/vpa-mappings/apply-pairings", json={"pairings": [
        {"txn_id": t.id, "customer_id": 5, "customer_type": "iop", "customer_name": "Parthiban"}]})
    assert (r.json()["paired"], r.json()["created"], r.json()["skipped"]) == (1, 1, [])
    assert mappings(session) == [("parthi@upi", 5, "iop", "Parthiban")]
    assert txn(session, t.id) == (5, "iop")


def test_nothing_else_in_the_database_changes(client, session):                                      # U-09
    t = add_txn(session, "r1", vpa="parthi@upi", name="PARTHIBAN", amount=500, mapped=3, mtype="iop")
    add_vpa(session, "parthi@upi", 3, "iop", "sathiskumar")
    unlink(client, t.id)
    session.expire_all()
    row = session.exec(select(UpiTransaction).where(UpiTransaction.id == t.id)).one()
    assert (row.upi_ref_no, float(row.amount), row.sender_vpa, row.sender_name) == ("r1", 500.0, "parthi@upi", "PARTHIBAN")
    assert session.exec(select(UpiVpaMapping)).all() == []
