"""Voice endpoints. Integration tests: they need PostgreSQL and Redis (see conftest.py).

The speech worker is replaced by a fake that exists only in these tests. The routes,
authentication, CSRF, scopes, body limit, the pipeline's audio checks, rate limiting and the
metadata written to the database are the real ones. Nothing here measures transcription quality.
"""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.web.spa import HTML_CSP
from tests.conftest import make_settings
from tests.helpers import bearer, create_project_and_key, db_fetch, signed_in_account
from tests.voice_double import TRANSCRIPT, FakeAsr, make_wav

MAX_UPLOAD = 200_000
LIMIT_PER_MINUTE = 6


@pytest.fixture(scope="module")
def voice(migrated_database):
    app = create_app(
        make_settings(voice_max_upload_bytes=MAX_UPLOAD, voice_rate_limit_per_minute=LIMIT_PER_MINUTE)
    )
    with TestClient(app) as test_client:
        asr = FakeAsr()
        app.state.voice.set_provider(asr)
        yield test_client, asr


@pytest.fixture(scope="module")
def bare(migrated_database):
    """An application with no speech worker connected, as a fresh deployment is."""
    with TestClient(create_app(make_settings())) as test_client:
        yield test_client


def project_for(account) -> str:
    response = account.request("POST", "/api/v1/projects", json={"name": "Voice project"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def browser_post(account, project_id, audio, *, language="yo", content_type="audio/wav", **kwargs):
    return account.request(
        "POST",
        f"/api/v1/voice/transcriptions?project_id={project_id}&language={language}",
        content=audio,
        headers={"Content-Type": content_type},
        **kwargs,
    )


def key_post(client, key, audio, *, language="yo", content_type="audio/wav") -> httpx.Response:
    client.cookies.clear()
    response = client.post(
        f"/api/v1/audio/transcriptions?language={language}",
        content=audio,
        headers={**bearer(key), "Content-Type": content_type},
    )
    client.cookies.clear()
    return response


def test_the_page_may_play_back_a_recording_but_the_policy_is_otherwise_unchanged():
    assert "media-src 'self' blob:" in HTML_CSP
    assert "default-src 'self'" in HTML_CSP and "script-src 'self'" in HTML_CSP
    assert "unsafe-inline" not in HTML_CSP and "unsafe-eval" not in HTML_CSP


def test_a_signed_in_person_can_transcribe_and_only_metadata_is_stored(voice):
    client, asr = voice
    account = signed_in_account(client)
    project_id = project_for(account)
    wav = make_wav(1.0)
    response = browser_post(account, project_id, wav)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == TRANSCRIPT and body["language"] == "yo"
    assert body["model"] == "NCAIR1/Yoruba-ASR" and body["attribution"]
    assert body["audio"] == {"format": "wav", "size_bytes": len(wav), "duration_seconds": 1.0}
    assert [step["name"] for step in body["steps"]] == ["validate", "asr"]
    assert body["request_id"] == response.headers["x-request-id"]
    assert response.headers["x-ratelimit-limit"] == str(LIMIT_PER_MINUTE)
    assert asr.calls[-1]["format"] == "wav" and asr.calls[-1]["language"] == "yo"

    (voice_row,) = db_fetch("select * from voice_requests where request_id = $1", body["request_id"])
    assert voice_row["status"] == "ok" and voice_row["language"] == "yo"
    assert voice_row["asr_model"] == "NCAIR1/Yoruba-ASR" and voice_row["audio_mime"] == "wav"
    assert voice_row["audio_bytes"] == len(wav) and voice_row["transcript_chars"] == len(TRANSCRIPT)
    assert voice_row["api_key_id"] is None and str(voice_row["user_id"]) == account.user_id
    (log_row,) = db_fetch("select * from requests where request_id = $1", body["request_id"])
    assert log_row["source"] == "voice" and log_row["outcome"] == "success"
    assert log_row["endpoint"] == "/api/v1/voice/transcriptions" and log_row["language"] == "yo"
    assert log_row["model"] == "NCAIR1/Yoruba-ASR" and log_row["status_code"] == 200
    for row in (voice_row, log_row):
        assert all(TRANSCRIPT not in str(value) for value in row.values())


def test_the_developer_api_needs_a_key_with_the_asr_scope(voice):
    client, _ = voice
    account = signed_in_account(client)
    _, allowed = create_project_and_key(account, scopes=["asr.transcribe"])
    _, chat_only = create_project_and_key(account, scopes=["chat.create"])
    wav = make_wav()
    ok = key_post(client, allowed["key"], wav)
    assert ok.status_code == 200, ok.text
    assert ok.json()["text"] == TRANSCRIPT and "steps" not in ok.json()
    (row,) = db_fetch("select * from voice_requests where request_id = $1", ok.json()["request_id"])
    assert str(row["api_key_id"]) == allowed["id"] and row["user_id"] is None

    forbidden = key_post(client, chat_only["key"], wav)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "insufficient_scope"
    client.cookies.clear()
    anonymous = client.post("/api/v1/audio/transcriptions?language=yo", content=wav)
    assert anonymous.status_code == 401
    # A browser session is not an API key.
    session_only = account.request(
        "POST",
        "/api/v1/audio/transcriptions?language=yo",
        content=wav,
        headers={"Content-Type": "audio/wav"},
    )
    assert session_only.status_code == 401


def test_with_no_worker_connected_the_answer_is_an_honest_503_and_it_is_logged(bare):
    account = signed_in_account(bare)
    project_id = project_for(account)
    response = browser_post(account, project_id, make_wav())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_not_configured"
    (row,) = db_fetch(
        "select * from voice_requests where request_id = $1", response.json()["error"]["request_id"]
    )
    assert row["status"] == "error" and row["error_code"] == "provider_not_configured"
    status = account.request("GET", "/api/v1/voice/status").json()
    assert status["state"] == "not_configured" and status["configured"] is False


def test_status_lists_limits_and_the_official_models_and_needs_a_session(voice):
    client, _ = voice
    client.cookies.clear()
    assert client.get("/api/v1/voice/status").status_code == 401
    account = signed_in_account(client)
    status = account.request("GET", "/api/v1/voice/status").json()
    assert status["state"] == "ready" and status["configured"] is True
    assert status["limits"]["max_upload_bytes"] == MAX_UPLOAD
    assert status["limits"]["max_seconds"] == 30
    assert set(status["limits"]["formats"]) == {"wav", "mp3", "ogg", "flac", "mp4", "webm"}
    models = {item["code"]: item for item in status["languages"]}
    assert set(models) == {"en", "ha", "ig", "yo"}
    assert models["yo"]["model"] == "NCAIR1/Yoruba-ASR" and models["yo"]["attribution"]
    assert models["ha"]["model"] == "NCAIR1/Hausa-ASR"


def test_bad_requests_get_clear_errors(voice):
    client, asr = voice
    account = signed_in_account(client)
    project_id = project_for(account)
    before = len(asr.calls)

    multipart = browser_post(account, project_id, b"--x", content_type="multipart/form-data; boundary=x")
    assert multipart.status_code == 415 and multipart.json()["error"]["code"] == "unsupported_media_type"
    not_audio = browser_post(account, project_id, b"definitely not audio" * 50)
    assert not_audio.status_code in (415, 422) and not_audio.json()["error"]["code"].startswith("audio_")
    empty = browser_post(account, project_id, b"")
    assert empty.json()["error"]["code"] == "audio_empty"
    mismatch = browser_post(account, project_id, make_wav(), content_type="audio/mpeg")
    assert mismatch.json()["error"]["code"] == "audio_signature_mismatch"
    language = browser_post(account, project_id, make_wav(), language="klingon")
    assert language.status_code == 422 and language.json()["error"]["code"] == "language_unsupported"
    missing = account.request(
        "POST",
        f"/api/v1/voice/transcriptions?project_id={project_id}",
        content=make_wav(),
        headers={"Content-Type": "audio/wav"},
    )
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "validation_error"
    assert len(asr.calls) == before  # nothing invalid reached the worker

    stranger = signed_in_account(client)
    foreign = browser_post(stranger, project_id, make_wav())
    assert foreign.status_code == 404 and foreign.json()["error"]["code"] == "project_not_found"
    no_csrf = browser_post(account, project_id, make_wav(), with_csrf=False)
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "csrf_validation_failed"


def test_size_limits_are_enforced_with_two_distinct_answers(voice):
    client, asr = voice
    account = signed_in_account(client)
    project_id = project_for(account)
    before = len(asr.calls)
    slightly_over = make_wav(7.0)  # about 224 KB: over 200 KB, within the 64 KiB allowance
    assert MAX_UPLOAD < len(slightly_over) < MAX_UPLOAD + 65_536
    friendly = browser_post(account, project_id, slightly_over)
    assert friendly.status_code == 413 and friendly.json()["error"]["code"] == "audio_too_large"
    far_over = browser_post(account, project_id, b"\x00" * (MAX_UPLOAD + 70_000))
    assert far_over.status_code == 413 and far_over.json()["error"]["code"] == "payload_too_large"
    too_long = browser_post(account, project_id, make_wav(31.0, rate=2_000))
    assert too_long.status_code in (413, 415, 422)  # too long, or a rate the checks refuse
    assert len(asr.calls) == before


def test_audio_requests_are_rate_limited_per_person(voice):
    client, _ = voice
    account = signed_in_account(client)
    project_id = project_for(account)
    for _ in range(LIMIT_PER_MINUTE):
        assert browser_post(account, project_id, make_wav()).status_code == 200
    limited = browser_post(account, project_id, make_wav())
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) >= 1
    other = signed_in_account(client)
    assert browser_post(other, project_for(other), make_wav()).status_code == 200


def test_recent_requests_list_metadata_only_and_belong_to_the_owner(voice):
    client, _ = voice
    account = signed_in_account(client)
    project_id = project_for(account)
    first = browser_post(account, project_id, make_wav()).json()
    bad = browser_post(account, project_id, b"not audio" * 10).json()
    rows = account.request("GET", f"/api/v1/projects/{project_id}/voice/requests").json()["data"]
    assert [row["request_id"] for row in rows] == [bad["error"]["request_id"], first["request_id"]]
    assert [row["status"] for row in rows] == ["error", "ok"]
    assert rows[1]["via"] == "browser" and rows[1]["audio_format"] == "wav"
    assert rows[1]["transcript_chars"] == len(TRANSCRIPT)
    assert TRANSCRIPT not in str(rows)
    assert len(account.request("GET", f"/api/v1/projects/{project_id}/voice/requests?limit=1").json()["data"]) == 1
    stranger = signed_in_account(client)
    assert stranger.request("GET", f"/api/v1/projects/{project_id}/voice/requests").status_code == 404
    unknown = account.request("GET", f"/api/v1/projects/{uuid.uuid4()}/voice/requests")
    assert unknown.status_code == 404
