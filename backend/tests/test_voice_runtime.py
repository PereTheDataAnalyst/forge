"""Voice runtime: building the worker client, parallel upload limit, and registration."""

import asyncio
from types import SimpleNamespace

from forge_integrations.errors import ForgeError
from forge_integrations.voice import HttpAsrProvider

from app.core.scopes import KNOWN_SCOPES
from app.modules.voice import runtime as voice_runtime
from app.modules.voice.runtime import VoiceRuntime, build_provider
from app.modules.voice.service import resolve_language

TOKEN = SimpleNamespace(get_secret_value=lambda: "t" * 40)
PUBLIC_URL = "https://93.184.216.34"  # a public address, so no DNS lookup is needed


def _settings(**overrides):
    base = dict(
        asr_service_url=PUBLIC_URL,
        asr_service_token=TOKEN,
        asr_allowed_hosts="",
        asr_allow_private_network=False,
        asr_allow_plaintext_remote=False,
        asr_timeout_seconds=30.0,
        asr_configured=True,
        voice_max_concurrent=2,
        voice_max_upload_bytes=1_000_000,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _expect_refusal(settings):
    try:
        build_provider(settings)
    except ForgeError as exc:
        return exc
    raise AssertionError("expected the worker address to be refused")


def test_a_public_https_worker_is_accepted():
    assert isinstance(build_provider(_settings()), HttpAsrProvider)


def test_a_private_or_loopback_worker_needs_an_explicit_switch():
    for url in ("https://10.0.0.5", "https://192.168.1.20", "http://127.0.0.1:8090"):
        assert _expect_refusal(_settings(asr_service_url=url)).code == "outbound_blocked", url
    ok = build_provider(_settings(asr_service_url="http://127.0.0.1:8090", asr_allow_private_network=True))
    assert isinstance(ok, HttpAsrProvider)


def test_plain_http_to_a_public_host_is_refused_unless_allowed():
    url = "http://93.184.216.34"
    assert _expect_refusal(_settings(asr_service_url=url)).code == "outbound_blocked"
    assert isinstance(build_provider(_settings(asr_service_url=url, asr_allow_plaintext_remote=True)), HttpAsrProvider)


def test_the_worker_host_is_always_allowed_and_extra_hosts_are_added():
    # The URL's own host needs no entry in ASR_ALLOWED_HOSTS.
    assert isinstance(build_provider(_settings(asr_allowed_hosts="")), HttpAsrProvider)
    assert isinstance(build_provider(_settings(asr_allowed_hosts=" other.example , ")), HttpAsrProvider)


def test_an_unconfigured_runtime_has_no_provider_and_makes_no_attempt():
    def factory(settings):
        raise AssertionError("must not be called")

    runtime = VoiceRuntime(_settings(asr_service_url=None, asr_service_token=None, asr_configured=False), factory=factory)
    assert not runtime.configured
    assert asyncio.run(runtime.get_provider()) is None and runtime.error is None


def test_a_provider_that_cannot_be_built_is_reported_and_retried_until_it_works():
    attempts = []

    def factory(settings):
        attempts.append(1)
        if len(attempts) < 3:
            raise ForgeError("outbound_blocked", "dns", http_status=403)
        return "provider"

    runtime = VoiceRuntime(_settings(), factory=factory)
    assert runtime.configured
    assert asyncio.run(runtime.get_provider()) is None and runtime.error == "outbound_blocked"
    assert asyncio.run(runtime.get_provider()) is None and runtime.error == "outbound_blocked"
    assert asyncio.run(runtime.get_provider()) == "provider" and runtime.error is None
    assert asyncio.run(runtime.get_provider()) == "provider" and len(attempts) == 3


def test_a_value_error_from_the_factory_is_a_configuration_problem_not_a_crash():
    def factory(settings):
        raise ValueError("bad token characters")

    runtime = VoiceRuntime(_settings(), factory=factory)
    assert asyncio.run(runtime.get_provider()) is None
    assert runtime.error == "invalid_configuration"


def test_an_injected_provider_is_used_without_any_configuration():
    runtime = VoiceRuntime(_settings(asr_configured=False))
    runtime.set_provider("fake")
    assert runtime.configured and asyncio.run(runtime.get_provider()) == "fake"


def test_the_limit_on_parallel_uploads_holds_and_release_never_goes_negative():
    runtime = VoiceRuntime(_settings(voice_max_concurrent=2))
    assert runtime.try_acquire() and runtime.try_acquire() and not runtime.try_acquire()
    runtime.release()
    assert runtime.try_acquire()
    for _ in range(5):
        runtime.release()
    assert runtime._active == 0


def test_languages_resolve_to_the_official_models_and_unknown_ones_are_a_clear_422():
    assert resolve_language("Yoruba").repo_id == "NCAIR1/Yoruba-ASR"
    assert resolve_language("ha").repo_id == "NCAIR1/Hausa-ASR"
    try:
        resolve_language("klingon")
    except Exception as exc:
        assert exc.status_code == 422 and exc.code == "language_unsupported"
    else:
        raise AssertionError("must raise")


def test_register_adds_the_scope_and_raises_the_body_limit_for_audio_paths_only():
    app = SimpleNamespace(state=SimpleNamespace(settings=_settings(), body_limit_overrides={}))
    voice_runtime.register(app)
    assert "asr.transcribe" in KNOWN_SCOPES
    limit = 1_000_000 + 64 * 1024
    assert app.state.body_limit_overrides == {"/api/v1/audio": limit, "/api/v1/voice": limit}
    assert isinstance(app.state.voice, VoiceRuntime)
