"""
Training data -> Google Drive (OCR page + table, Voice recordings + table).

Covers: folder layout (fintech at the top of My Drive), the ONE csv format shared by OCR and Voice, date normalisation,
validation, Drive failures, the removed dataset module, and — the hard rule — that nothing is left on the server's disk.
"""
import csv
import importlib
import io
import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.models.upi import DriveSettings
from app.schemas.training_data import CSV_COLUMNS
from app.services import drive_service, training_data_service as svc
from tests.fake_drive import FakeDrive

JPEG = b"\xff\xd8\xff\xe0 pretend this is a jpeg"
WAV = b"RIFF$\x00\x00\x00WAVEfmt pretend wav"


@pytest.fixture()
def drive(monkeypatch, session):
    """Drive connected (a token is stored) and replaced by an in-memory fake."""
    fake = FakeDrive()
    session.add(DriveSettings(id=1, email="owner@example.com", access_token="tok", refresh_token="ref"))
    session.commit()
    monkeypatch.setattr(drive_service, "_build_drive_service", lambda d, s: fake)
    return fake


def ocr_rows():
    return [
        {"customer": "Selvam", "date": "17-10-2026", "amount": 500, "type": "EDI", "mode": "ONLINE",
         "raw_text": "Selvm", "origin": "matched"},
        {"customer": "Kannan anna", "date": "17-10-2026", "amount": 250.5, "type": "iop", "mode": "cash",
         "raw_text": "Kanan anna", "origin": "picked"},
    ]


def post_ocr(client, rows=None, page=1, image=JPEG, name="page.jpg", ctype="image/jpeg", **form):
    return client.post(
        "/api/training-data/ocr",
        files={"image": (name, image, ctype)},
        data={"rows": json.dumps(ocr_rows() if rows is None else rows), "page": str(page), **form},
    )


def voice_rows(source="rec1"):
    return [
        {"customer": "Selvam", "date": "2026-10-17", "amount": 400, "type": "EDI", "mode": "CASH",
         "raw_text": "selvem", "source_file": source, "origin": "picked"},
        {"customer": "Kannan", "date": "2026-10-17", "amount": 200, "type": "EDI", "mode": "ONLINE",
         "raw_text": "kannan", "source_file": "rec2", "origin": "matched"},
    ]


def post_voice(client, rows=None, recordings=None, transcriptions=None, **form):
    recordings = recordings if recordings is not None else [("rec1.wav", WAV), ("rec2.wav", WAV + b"2")]
    return client.post(
        "/api/training-data/voice",
        files=[("recordings", (n, b, "audio/wav")) for n, b in recordings],
        data={"rows": json.dumps(voice_rows() if rows is None else rows),
              "transcriptions": json.dumps({"rec1": "செல்வம் 400 கண்ணன் 200", "rec2": ""} if transcriptions is None else transcriptions),
              **form},
    )


def read_csv(data: bytes):
    return list(csv.reader(io.StringIO(data.decode("utf-8"))))


# ── OCR ───────────────────────────────────────────────────────────────────────

def test_ocr_saves_image_then_csv_under_fintech_ocr_date(client, drive):            # T2-01, T2-05
    r = post_ocr(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] == 2 and body["folder"] == "fintech/ocr/2026-10-17"

    image, table = drive.uploads                                                     # image FIRST: a csv never points at a missing file
    assert image["path"] == table["path"] == "fintech/ocr/2026-10-17"
    assert image["data"] == JPEG and image["mimetype"] == "image/jpeg" and image["name"].endswith("_p001.jpg")
    assert table["name"] == image["name"].rsplit(".", 1)[0] + ".csv" and table["mimetype"] == "text/csv"
    assert body["files"] == [image["name"], table["name"]]

    # `fintech` is a top-level folder in My Drive — not inside gg_fin
    assert drive.folders() == ["fintech", "fintech/ocr", "fintech/ocr/2026-10-17"]
    assert not any("gg_fin" in f for f in drive.folders())

    rows = read_csv(table["data"])
    assert rows[0] == CSV_COLUMNS
    assert rows[1] == ["Selvam", "2026-10-17", "500", "EDI", "ONLINE", "Selvm", image["name"], "matched"]
    assert rows[2] == ["Kannan anna", "2026-10-17", "250.50", "IOP", "CASH", "Kanan anna", image["name"], "picked"]  # lower-case type/mode accepted


def test_ocr_second_upload_reuses_the_folders(client, drive):                         # T2-01
    post_ocr(client)
    folders = drive.folders()
    post_ocr(client, page=2)
    assert drive.folders() == folders                                                # nothing created twice
    assert len(drive.uploads) == 4 and drive.uploads[2]["name"].endswith("_p002.jpg")


def test_ocr_folder_date_is_the_earliest_row_date_unless_requested(client, drive):
    rows = ocr_rows()
    rows[0]["date"] = "18-10-2026"
    post_ocr(client, rows=rows)
    assert drive.uploads[-1]["path"] == "fintech/ocr/2026-10-17"
    post_ocr(client, rows=rows, folder_date="2026-10-16")
    assert drive.uploads[-1]["path"] == "fintech/ocr/2026-10-16"


def test_png_image_is_accepted_and_keeps_its_type(client, drive):
    assert post_ocr(client, name="page.png", ctype="image/png").status_code == 200
    assert drive.uploads[0]["name"].endswith(".png") and drive.uploads[0]["mimetype"] == "image/png"


def test_two_submits_in_the_same_second_never_overwrite_each_other(client, drive):
    post_ocr(client)
    post_ocr(client)
    names = [u["name"] for u in drive.uploads]
    assert len(set(names)) == 4


# ── Voice ─────────────────────────────────────────────────────────────────────

def test_voice_saves_recordings_transcriptions_and_one_csv(client, drive):           # T2-02
    r = post_voice(client)
    assert r.status_code == 200, r.text
    assert r.json()["folder"] == "fintech/voice/2026-10-17"

    wavs = [u for u in drive.uploads if u["name"].endswith(".wav")]
    txts = [u for u in drive.uploads if u["name"].endswith(".txt")]
    tables = [u for u in drive.uploads if u["name"].endswith(".csv")]
    assert len(wavs) == 2 and len(tables) == 1
    assert len(txts) == 1                                                            # rec2 had no transcription -> no empty .txt
    assert all(u["path"] == "fintech/voice/2026-10-17" for u in drive.uploads)
    assert wavs[0]["data"] == WAV and wavs[0]["name"].endswith("_rec01.wav") and wavs[1]["name"].endswith("_rec02.wav")
    assert txts[0]["data"].decode("utf-8") == "செல்வம் 400 கண்ணன் 200"
    assert txts[0]["name"] == wavs[0]["name"].rsplit(".", 1)[0] + ".txt"
    assert drive.uploads[-1] is tables[0]                                            # the csv goes last

    rows = read_csv(tables[0]["data"])
    assert rows[1][6] == wavs[0]["name"] and rows[2][6] == wavs[1]["name"]            # source_file = the real uploaded names


def test_voice_csv_has_exactly_the_ocr_columns(client, drive):                         # T2-06
    post_ocr(client)
    post_voice(client)
    ocr_csv = next(u for u in drive.uploads if u["path"].startswith("fintech/ocr") and u["name"].endswith(".csv"))
    voice_csv = next(u for u in drive.uploads if u["path"].startswith("fintech/voice") and u["name"].endswith(".csv"))
    assert read_csv(ocr_csv["data"])[0] == read_csv(voice_csv["data"])[0] == CSV_COLUMNS
    v = read_csv(voice_csv["data"])[1]
    assert v[:6] == ["Selvam", "2026-10-17", "400", "EDI", "CASH", "selvem"] and v[7] == "picked"


def test_voice_unknown_recording_in_rows_is_rejected(client, drive):
    r = post_voice(client, rows=voice_rows(source="rec9"))
    assert r.status_code == 400 and "rec9" in r.text and drive.uploads == []


def test_voice_unsupported_audio_type_and_duplicate_names_are_rejected(client, drive):
    assert post_voice(client, recordings=[("rec1.exe", WAV), ("rec2.wav", WAV)]).status_code == 400
    assert post_voice(client, recordings=[("rec1.wav", WAV), ("rec1.wav", WAV)]).status_code == 400
    assert post_voice(client, transcriptions="not json").status_code == 400
    assert drive.uploads == []


# ── validation ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("patch", [
    {"customer": ""}, {"type": "XYZ"}, {"mode": "CARD"}, {"date": "tomorrow"}, {"amount": -5}, {"origin": "guess"},
])
def test_invalid_rows_are_rejected_with_422(client, drive, patch):
    rows = ocr_rows()
    rows[0].update(patch)
    assert post_ocr(client, rows=rows).status_code == 422
    assert drive.uploads == []


def test_empty_rows_and_bad_images_are_rejected(client, drive):
    assert post_ocr(client, rows=[]).status_code == 400
    assert post_ocr(client, name="page.gif", ctype="image/gif").status_code == 400
    assert post_ocr(client, image=b"").status_code == 400
    assert drive.uploads == []


def test_endpoints_require_login():
    from app.main import app
    anonymous = TestClient(app)
    assert anonymous.post("/api/training-data/ocr", files={"image": ("p.jpg", JPEG, "image/jpeg")},
                          data={"rows": "[]"}).status_code in (401, 403)


# ── Drive problems never crash — they are reported ────────────────────────────

def test_drive_not_connected_is_a_409_with_a_clear_message(client):                   # T2-10
    r = post_ocr(client)                                                              # no DriveSettings row at all
    assert r.status_code == 409 and "not connected" in r.json()["detail"].lower()


def test_drive_token_that_cannot_refresh_is_a_409(client, session, monkeypatch):      # T2-10
    session.add(DriveSettings(id=1, access_token="tok", refresh_token="ref"))
    session.commit()

    def refuse(d, s):
        raise RuntimeError("Drive token refresh failed — please reconnect Google Drive.")
    monkeypatch.setattr(drive_service, "_build_drive_service", refuse)
    r = post_ocr(client)
    assert r.status_code == 409 and "reconnect" in r.json()["detail"].lower()


def test_drive_upload_error_is_a_502_and_no_csv_is_written_after_a_failed_image(client, drive):   # T2-10
    drive.fail_upload_named = ".jpg"
    r = post_ocr(client)
    assert r.status_code == 502 and "upload failed" in r.json()["detail"].lower()
    assert drive.uploads == []                                                        # the csv was not uploaded either


def test_voice_drive_error_is_a_502(client, drive):                                   # T2-10
    drive.fail_upload_named = "rec02"
    assert post_voice(client).status_code == 502


# ── the hard rule: nothing is stored on the server ────────────────────────────

def test_server_stores_nothing(client, drive, monkeypatch, tmp_path):                 # T2-04
    tmp, cwd = tmp_path / "tmp", tmp_path / "cwd"
    tmp.mkdir(); cwd.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    monkeypatch.chdir(cwd)

    big_image, big_audio = os.urandom(1_600_000), os.urandom(1_700_000)               # > 1 MB: the web framework buffers these on disk WHILE the request runs
    assert post_ocr(client, image=big_image).status_code == 200
    assert post_voice(client, recordings=[("rec1.wav", big_audio), ("rec2.wav", WAV)]).status_code == 200

    assert list(tmp.iterdir()) == [] and list(cwd.iterdir()) == []                    # no temp file and no dataset file is left behind
    assert not Path("/app/dataset").exists()
    assert {u["media_type"] for u in drive.uploads} == {"MediaIoBaseUpload"}          # uploaded from memory, never from a file (MediaFileUpload)
    assert big_image in [u["data"] for u in drive.uploads] and big_audio in [u["data"] for u in drive.uploads]


def test_no_leftover_dataset_folder_is_created_by_the_service_itself(drive, session, monkeypatch, tmp_path):   # T2-04
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    from app.schemas.training_data import TrainingRow
    row = TrainingRow(customer="A", date="2026-10-17", amount=1, type="EDI", mode="CASH")
    svc.save_ocr_page(session, image=JPEG, image_ext="jpg", page=1, rows=[row])
    svc.save_voice_batch(session, recordings=[svc.Recording("r1", "wav", WAV, "x")], rows=[row.model_copy(update={"source_file": "r1"})])
    assert list(tmp_path.iterdir()) == []


# ── the removed voice dataset module ──────────────────────────────────────────

def test_old_dataset_module_is_gone(client):                                          # T2-03
    from app.main import app
    for path in ("/api/dataset/stats", "/api/dataset/export", "/api/dataset/save-audio", "/api/dataset/save-metadata", "/api/dataset/clear"):
        assert client.get(path).status_code in (404, 405) and client.post(path).status_code in (404, 405)
    assert not any(getattr(r, "path", "").startswith("/api/dataset") for r in app.routes)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.api.dataset")


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_csv_roundtrips_commas_quotes_and_tamil():                                    # T2-05
    from app.schemas.training_data import TrainingRow
    row = TrainingRow(customer='Kumar, "K" செல்வம்', date="05-01-2026", amount=1234.0, type="edi", mode="online",
                      raw_text='line\nbreak', source_file="x.jpg", origin="manual")
    parsed = read_csv(svc.build_csv([row]))
    assert parsed[0] == CSV_COLUMNS
    assert parsed[1] == ['Kumar, "K" செல்வம்', "2026-01-05", "1234", "EDI", "ONLINE", "line\nbreak", "x.jpg", "manual"]
    assert not svc.build_csv([row]).startswith(b"\xef\xbb\xbf")                      # plain UTF-8, no BOM


@pytest.mark.parametrize("value,expected", [(500, "500"), (500.0, "500"), (250.5, "250.50"), (0.1 + 0.2, "0.30")])
def test_format_amount(value, expected):
    assert svc.format_amount(value) == expected
