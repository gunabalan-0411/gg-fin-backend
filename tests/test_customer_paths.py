"""
Every route by which a customer can be added / closed / renamed / deleted re-points the mappings (R-25).

The Customers tab (CustomersPage), the OCR "Customer Management" step (CustomerStepOverlay) and the undo/redo in both
call the very same endpoints (`customersApi.createEdi/updateEdi/deleteEdi/...` -> `/api/customers/{edi|iop}`), so the HTTP
tests below are what both screens exercise. Repository tests cover the layer underneath.   (T-24, T-25, T-26, T-28)
"""
import pytest

from app.repositories.customer_repo import EdiCustomerRepo, IopCustomerRepo
from tests.helpers import add_mapping, pointer, rows

FT = ["EDI", "IOP"]
REPO = {"EDI": EdiCustomerRepo, "IOP": IopCustomerRepo}
URL = {"EDI": "/api/customers/edi", "IOP": "/api/customers/iop"}


def _payload(cid, name, **kw):
    return {"customer_id": cid, "customer_name": name, "loan_amount": 10000, **kw}


# ── repository layer ────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ft", FT)
def test_repo_create_update_delete_repoint(session, conn, ft):
    add_mapping(conn, "alias", ft, "Repo Person", "rp", cid=None)
    add_mapping(conn, "ai_mismatch", ft, "Repo Person", "rpp", cid=None)
    repo = REPO[ft](session)

    c = repo.create({"customer_id": 11, "customer_name": "Repo Person", "is_closed": False, "loan_amount": 1000})  # T-24
    assert pointer(conn, "alias", ft, "Repo Person", "rp") == (11, True)
    assert pointer(conn, "ai_mismatch", ft, "Repo Person", "rpp") == (11, True)

    repo.update(c, {"is_closed": True})                                                                            # T-25
    assert pointer(conn, "alias", ft, "Repo Person", "rp") == (11, False)
    repo.update(c, {"is_closed": False})
    assert pointer(conn, "alias", ft, "Repo Person", "rp") == (11, True)

    repo.update(c, {"customer_name": "Repo Person Renamed"})                                                       # rename
    assert rows(conn, "alias", ft) == [("Repo Person Renamed", "rp", 11)]
    assert rows(conn, "ai_mismatch", ft) == [("Repo Person Renamed", "rpp", 11)]

    repo.delete(c)                                                                                                 # T-26
    assert pointer(conn, "alias", ft, "Repo Person Renamed", "rp") == (11, False)
    assert len(rows(conn, "alias", ft)) == 1                                                                       # row kept


# ── HTTP layer: what the Customers tab and the OCR customer step really call ───────────────────────────────
@pytest.mark.parametrize("ft", FT)
def test_http_customer_lifecycle_keeps_mappings_in_step(client, conn, ft):
    url = URL[ft]
    add_mapping(conn, "alias", ft, "Ganesh", "Ganes", cid=None)
    add_mapping(conn, "ai_mismatch", ft, "Ganesh", "Ganeshh", cid=None)

    # Add Customer (Customers tab / OCR customer step)
    r = client.post(url, json=_payload(5, "Ganesh"))
    assert r.status_code == 201, r.text
    assert pointer(conn, "alias", ft, "Ganesh", "Ganes") == (5, True)
    assert pointer(conn, "ai_mismatch", ft, "Ganesh", "Ganeshh") == (5, True)

    # close, then re-open
    assert client.patch(f"{url}/5", json={"is_closed": True}).status_code == 200
    assert pointer(conn, "alias", ft, "Ganesh", "Ganes") == (5, False)
    assert client.patch(f"{url}/5", json={"is_closed": False}).status_code == 200
    assert pointer(conn, "alias", ft, "Ganesh", "Ganes") == (5, True)

    # edit the name in the customer form
    assert client.patch(f"{url}/5", json={"customer_name": "Ganesh Kumar"}).status_code == 200
    assert rows(conn, "alias", ft) == [("Ganesh Kumar", "Ganes", 5)]
    assert pointer(conn, "ai_mismatch", ft, "Ganesh Kumar", "Ganeshh") == (5, True)

    # delete (plain)
    assert client.delete(f"{url}/5").status_code == 204
    assert pointer(conn, "alias", ft, "Ganesh Kumar", "Ganes") == (5, False)
    assert len(rows(conn, "alias", ft)) == 1 and len(rows(conn, "ai_mismatch", ft)) == 1

    # the person comes back as a NEW account with a new id and the same name
    assert client.post(url, json=_payload(9, "Ganesh Kumar")).status_code == 201
    assert pointer(conn, "alias", ft, "Ganesh Kumar", "Ganes") == (9, True)
    assert pointer(conn, "ai_mismatch", ft, "Ganesh Kumar", "Ganeshh") == (9, True)


@pytest.mark.parametrize("ft", FT)
def test_http_delete_with_resequence(client, conn, ft):
    url = URL[ft]
    for i, n in enumerate(["Aa", "Bb", "Cc"], start=1):
        assert client.post(url, json=_payload(i, n)).status_code == 201
    add_mapping(conn, "alias", ft, "Aa", "a", cid=1)
    add_mapping(conn, "alias", ft, "Cc", "c", cid=3)
    assert client.delete(f"{url}/2?resequence=true").status_code == 204
    assert pointer(conn, "alias", ft, "Aa", "a") == (1, True)
    assert pointer(conn, "alias", ft, "Cc", "c") == (2, True)           # shifted from 3 to 2


@pytest.mark.parametrize("ft", FT)
def test_http_balance_changes_do_not_disturb_mappings(client, conn, ft):
    """Editing the loan amount re-syncs the balance (and fires UPDATE) but must leave pointers alone."""
    url = URL[ft]
    assert client.post(url, json=_payload(1, "Steady")).status_code == 201
    add_mapping(conn, "alias", ft, "Steady", "s", cid=1)
    assert client.patch(f"{url}/1", json={"loan_amount": 25000}).status_code == 200
    assert pointer(conn, "alias", ft, "Steady", "s") == (1, True)


def test_http_edi_and_iop_with_the_same_name_do_not_interfere(client, conn):
    assert client.post(URL["EDI"], json=_payload(1, "Twin")).status_code == 201
    assert client.post(URL["IOP"], json=_payload(1, "Twin")).status_code == 201
    add_mapping(conn, "alias", "EDI", "Twin", "t", cid=1)
    add_mapping(conn, "alias", "IOP", "Twin", "t", cid=1)
    assert client.patch(f"{URL['EDI']}/1", json={"is_closed": True}).status_code == 200
    assert pointer(conn, "alias", "EDI", "Twin", "t") == (1, False)
    assert pointer(conn, "alias", "IOP", "Twin", "t") == (1, True)
