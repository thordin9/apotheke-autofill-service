"""Feature 7: model choice (ALLOWED_MODELS, context.model, model_used) and
image size presets (IMAGE_SIZES), on the echo and Ollama backends."""

from __future__ import annotations

import json
import threading
import time

import pytest
from conftest import assert_contract, post_image
from ollama_mock import FakeOllama

from app.backends.selection import allowlist, cold_wait_fits, model_key, resolve_model
from app.settings import parse_image_sizes, parse_models

QWEN = "qwen2.5vl-jetson:3b"
MOON = "moondream"
QWEN3 = "qwen3-vl:2b"
OLD_INFO_KEYS = {"name", "version", "capabilities", "backend", "max_image_bytes"}


def ctx(**extra):
    return json.dumps({"categories": ["Tools"], "tags": [], **extra})


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# -- unit: env parsing and selection ----------------------------------------


def test_parse_models_drops_blanks_and_repeats():
    assert parse_models(" a , ,b,a,") == ("a", "b")
    assert parse_models("") == ()


@pytest.mark.parametrize(
    "raw, want",
    [
        ("448,672*,1024", ((448, False), (672, True), (1024, False))),
        ("1024, 448 ,672", ((448, False), (672, False), (1024, True))),  # largest
        ("672*,672", ((672, True),)),
        ("", ()),
        (" , ", ()),
    ],
)
def test_parse_image_sizes(raw, want):
    assert parse_image_sizes(raw) == want


@pytest.mark.parametrize("raw", ["abc", "448,big*", "10", "9000", "448*,672*"])
def test_parse_image_sizes_rejects_bad_values(raw):
    with pytest.raises(RuntimeError, match="IMAGE_SIZES"):
        parse_image_sizes(raw)


def test_bad_image_sizes_fail_at_startup(make_client):
    with pytest.raises(RuntimeError, match="IMAGE_SIZES"):
        make_client(IMAGE_SIZES="448*,672*")


def test_allowlist_puts_default_first_once():
    assert allowlist(QWEN, (MOON, QWEN, "moondream:latest")) == [QWEN, MOON]


def test_model_key():
    assert model_key("moondream") == "moondream:latest"
    assert model_key(" qwen:3b ") == "qwen:3b"
    assert model_key("reg.io:5000/m") == "reg.io:5000/m:latest"


@pytest.mark.parametrize(
    "requested, installed, want",
    [
        (None, {"moondream:latest"}, QWEN),  # old client: default
        ("", {"moondream:latest"}, QWEN),
        (MOON, {"moondream:latest"}, MOON),  # allowlisted + installed
        ("moondream:latest", {"moondream:latest"}, MOON),  # allowlist spelling
        (MOON, set(), QWEN),  # not installed: fallback
        (MOON, None, QWEN),  # installed unknown: fallback
        ("llava:7b", {"llava:7b"}, QWEN),  # unknown (not allowlisted): fallback
        (QWEN, set(), QWEN),
        (42, {"moondream:latest"}, QWEN),
    ],
)
def test_resolve_model(requested, installed, want):
    assert resolve_model(requested, QWEN, [QWEN, MOON], installed) == want


def test_cold_wait_fits_uses_90_percent_of_timeout():
    # Qwen on the Jetson: 15 s load + 13.5 s answer.
    assert not cold_wait_fits(15, 13.5, 25)
    assert cold_wait_fits(15, 13.5, 45)
    assert cold_wait_fits(9, 0, 10) and not cold_wait_fits(9.1, 0, 10)


# -- contract: old clients see no change -------------------------------------


def test_info_unchanged_without_new_env(client):
    body = assert_contract(client.get("/v1/info"), "/v1/info")
    assert set(body) == OLD_INFO_KEYS


def test_suggest_without_model_unchanged_plus_model_used(client):
    body = assert_contract(post_image(client, context=ctx()), "/v1/suggest", "post")
    assert body["model_used"] == "echo"
    assert set(body) == {"name", "category", "tags", "description", "model_used"}


def test_context_model_ignored_without_allowed_models(make_client):
    c = make_client(ECHO_INSTALLED_MODELS=MOON)
    assert post_image(c, context=ctx(model=MOON)).json()["model_used"] == "echo"
    assert "models" not in c.get("/v1/info").json()


@pytest.mark.parametrize("bad", [42, ["m"], {"id": "m"}, True])
def test_context_model_must_be_a_string(client, bad):
    r = post_image(client, context=ctx(model=bad))
    assert r.status_code == 400
    assert assert_contract(r, "/v1/suggest", "post")["error"]["code"] == "bad_request"


def test_info_image_sizes(make_client):
    body = assert_contract(make_client(IMAGE_SIZES="448,672*,1024").get("/v1/info"), "/v1/info")
    assert body["image_sizes"] == [
        {"long_edge": 448, "recommended": False},
        {"long_edge": 672, "recommended": True},
        {"long_edge": 1024, "recommended": False},
    ]
    assert "models" not in body


def test_sanitize_keeps_only_string_model_used():
    from app.main import sanitize

    assert sanitize({"model_used": "m"}) == {"model_used": "m"}
    assert sanitize({"model_used": 3}) == {}
    assert sanitize({"model_used": " "}) == {}


def test_sanitize_models_drops_bad_entries():
    from app.main import sanitize_models

    assert sanitize_models("x") is None
    got = sanitize_models(
        [
            {"id": "a", "default": True, "loaded": False, "avg_latency_ms": 5, "x": 1},
            {"id": "b", "default": "yes", "loaded": "no", "avg_latency_ms": -1},
            {"id": ""},
            "c",
        ]
    )
    assert got == [
        {"id": "a", "default": True, "loaded": False, "avg_latency_ms": 5},
        {"id": "b", "default": False},
    ]


def _info_errors(body):
    from conftest import _registry, _schema_for
    from jsonschema import Draft202012Validator

    schema = _schema_for("/v1/info", "get", 200)
    return list(Draft202012Validator(schema, registry=_registry).iter_errors(body))


def test_info_schema_new_fields():
    base = {"name": "x", "version": "1", "capabilities": ["name"]}
    assert not _info_errors({**base, "models": [{"id": "m", "default": True}]})
    assert not _info_errors({**base, "image_sizes": [{"long_edge": 448, "recommended": True}]})
    assert _info_errors({**base, "models": [{"id": "m"}]})
    assert _info_errors({**base, "image_sizes": [{"long_edge": 10, "recommended": True}]})


# -- echo backend -------------------------------------------------------------


def test_echo_info_models(make_client):
    c = make_client(
        ALLOWED_MODELS=f"{MOON},{QWEN3},llava", ECHO_MODEL=QWEN, ECHO_INSTALLED_MODELS=MOON
    )
    body = assert_contract(c.get("/v1/info"), "/v1/info")
    assert body["ready"] is True
    assert body["models"] == [
        {"id": QWEN, "default": True, "loaded": True},
        {"id": MOON, "default": False, "loaded": False},
    ]


def test_echo_all_allowed_installed_by_default(make_client):
    c = make_client(ALLOWED_MODELS=f"{MOON},{QWEN3}", ECHO_DELAY_MS="40")
    models = c.get("/v1/info").json()["models"]
    assert [m["id"] for m in models] == ["echo", MOON, QWEN3]
    assert all(m["avg_latency_ms"] == 40 for m in models)


def test_echo_allowed_model_is_used(make_client):
    c = make_client(ALLOWED_MODELS=MOON)
    body = assert_contract(post_image(c, context=ctx(model=MOON)), "/v1/suggest", "post")
    assert body["model_used"] == MOON
    info = c.get("/v1/info").json()
    assert [m["loaded"] for m in info["models"]] == [False, True]


@pytest.mark.parametrize("model", ["llava:7b", QWEN3])
def test_echo_unknown_or_not_installed_falls_back(make_client, model):
    c = make_client(ALLOWED_MODELS=f"{MOON},{QWEN3}", ECHO_INSTALLED_MODELS=MOON)
    assert post_image(c, context=ctx(model=model)).json()["model_used"] == "echo"


def test_echo_switch_waits_when_it_fits(make_client):
    # Jetson numbers scaled by 0.1: 1.5 s load + 1.35 s answer fit 0.9 x 4.5 s.
    c = make_client(
        ALLOWED_MODELS=MOON, ECHO_LOAD_MS="1500", ECHO_DELAY_MS="1350", BACKEND_TIMEOUT_S="4.5"
    )
    t = time.monotonic()
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 200 and r.json()["model_used"] == MOON
    assert time.monotonic() - t >= 2.8
    # Now loaded: no load time again.
    t = time.monotonic()
    assert post_image(c, context=ctx(model=MOON)).status_code == 200
    assert time.monotonic() - t < 2.5


def test_echo_switch_gets_fast_503_when_it_does_not_fit(make_client):
    # ...but not 0.9 x 2.5 s: 503 at once, the load carries on.
    c = make_client(
        ALLOWED_MODELS=MOON, ECHO_LOAD_MS="1500", ECHO_DELAY_MS="1350", BACKEND_TIMEOUT_S="2.5"
    )
    t = time.monotonic()
    r = post_image(c, context=ctx(model=MOON))
    assert time.monotonic() - t < 1.0
    assert r.status_code == 503 and r.headers["Retry-After"] == "2"
    body = assert_contract(r, "/v1/suggest", "post")
    assert body["error"]["code"] == "backend_unavailable" and "loading" in body["error"]["message"]
    info = c.get("/v1/info").json()
    assert info["ready"] is False and not any(m["loaded"] for m in info["models"])
    c.app.state.backend._ready_at = 0.0  # pretend the load finished
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 200 and r.json()["model_used"] == MOON
    # Moondream evicted the default, so switching back is cold too.
    assert post_image(c, context=ctx()).status_code == 503


def test_echo_switch_back_to_default_is_cold(make_client):
    c = make_client(ALLOWED_MODELS=MOON, ECHO_LOAD_MS="5000", BACKEND_TIMEOUT_S="2")
    backend = c.app.state.backend
    backend._loaded = MOON  # a client picked moondream earlier
    r = post_image(c, context=ctx())
    assert r.status_code == 503 and r.headers["Retry-After"] == "5"
    assert backend._loaded == "echo"


def test_echo_without_load_time_switches_instantly(make_client):
    c = make_client(ALLOWED_MODELS=MOON)
    for m, want in ((MOON, MOON), (None, "echo"), (MOON, MOON)):
        assert post_image(c, context=ctx(model=m)).json()["model_used"] == want


# -- Ollama backend -----------------------------------------------------------


@pytest.fixture
def fake():
    f = FakeOllama()
    f.loaded = [QWEN]
    f.tags = [QWEN, "moondream:latest", "llava:7b"]
    yield f
    f.close()


@pytest.fixture
def oclient(make_client, fake):
    def _make(**env):
        env.setdefault("OLLAMA_URL", fake.url)
        env.setdefault("OLLAMA_WARMUP", "0")
        env.setdefault("OLLAMA_MODEL", QWEN)
        return make_client(BACKEND="ollama", **env)

    return _make


def test_ollama_info_unchanged_without_allowed_models(oclient, fake):
    body = assert_contract(oclient().get("/v1/info"), "/v1/info")
    assert set(body) == OLD_INFO_KEYS | {"ready"}
    assert fake.gets("/api/tags") == 0


def test_ollama_info_lists_installed_allowed_models(oclient, fake):
    c = oclient(ALLOWED_MODELS=f"{MOON},{QWEN3}", IMAGE_SIZES="448,672*,1024")
    body = assert_contract(c.get("/v1/info"), "/v1/info")
    assert body["models"] == [
        {"id": QWEN, "default": True, "loaded": True},
        {"id": MOON, "default": False, "loaded": False},
    ]  # qwen3-vl is not installed, llava is not allowed
    assert [s["long_edge"] for s in body["image_sizes"]] == [448, 672, 1024]


def test_ollama_installed_list_is_cached(oclient, fake):
    c = oclient(ALLOWED_MODELS=MOON)
    c.get("/v1/info")
    c.get("/v1/info")
    post_image(c, context=ctx(model=MOON))
    assert fake.gets("/api/tags") == 1


def test_ollama_tags_failure_offers_default_only(oclient, fake):
    fake.tags = None  # GET /api/tags answers 404
    c = oclient(ALLOWED_MODELS=MOON)
    assert [m["id"] for m in c.get("/v1/info").json()["models"]] == [QWEN]
    r = post_image(c, context=ctx(model=MOON))
    assert r.json()["model_used"] == QWEN and fake.chats()[-1]["model"] == QWEN


def test_ollama_tags_failure_keeps_last_good_list(oclient, fake):
    c = oclient(ALLOWED_MODELS=MOON)
    assert len(c.get("/v1/info").json()["models"]) == 2
    fake.tags = None
    c.app.state.backend._installed_at = float("-inf")  # cache expired
    assert len(c.get("/v1/info").json()["models"]) == 2


def test_ollama_suggest_uses_allowed_model(oclient, fake):
    fake.loaded = ["moondream:latest"]
    c = oclient(ALLOWED_MODELS=MOON)
    body = assert_contract(post_image(c, context=ctx(model=MOON)), "/v1/suggest", "post")
    assert body["model_used"] == MOON
    assert fake.chats()[-1]["model"] == MOON


@pytest.mark.parametrize("model", ["llava:7b", QWEN3, "", None])
def test_ollama_unknown_not_installed_or_absent_falls_back(oclient, fake, model):
    c = oclient(ALLOWED_MODELS=f"{MOON},{QWEN3}")
    body = post_image(c, context=ctx(model=model)).json()
    assert body["model_used"] == QWEN and fake.chats()[-1]["model"] == QWEN


def test_ollama_old_client_without_allowed_models(oclient, fake):
    c = oclient()
    body = post_image(c, context=ctx(model=MOON)).json()
    assert body["model_used"] == QWEN and fake.chats()[-1]["model"] == QWEN
    assert fake.gets("/api/tags") == 0


def test_ollama_switch_at_25_s_gets_fast_503_and_loads(oclient, fake):
    # Qwen loaded, client picks moondream: a cold start. Unmeasured, 15 s load
    # + 15 s answer do not fit 0.9 x 25 s, so 503 at once and load it.
    fake.track_loads = True
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25", OLLAMA_RETRY_AFTER_S="5")
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 503 and r.headers["Retry-After"] == "15"
    assert "moondream" in r.json()["error"]["message"]
    assert fake.chats() == []
    assert _wait(lambda: [g["model"] for g in fake.generates()] == [MOON])
    # Loaded now: the retry is served by moondream.
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 200 and r.json()["model_used"] == MOON


def test_ollama_switch_at_45_s_waits_for_the_load(oclient, fake):
    fake.track_loads = True
    fake.load_duration_ns = 2_000_000_000
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="45")
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 200 and r.json()["model_used"] == MOON
    assert fake.chats()[-1]["model"] == MOON and fake.generates() == []
    backend = c.app.state.backend
    assert backend.stats(MOON).load_s == 2.0 and backend.stats(QWEN).load_s is None


def test_ollama_switch_uses_that_models_timings(oclient, fake):
    # Default measured fast, moondream measured slow: only moondream's slow
    # load + answer decide whether a switch to it can wait.
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="40")
    b = c.app.state.backend
    b._record(1.0)
    b._record_load(15.0)
    b._record(30.0, MOON)
    assert b._cold_wait_fits() and not b._cold_wait_fits(MOON)
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 503 and "loading" in r.json()["error"]["message"]
    assert post_image(c, context=ctx()).status_code == 200


def test_ollama_same_model_needs_no_load(oclient, fake):
    fake.loaded = ["moondream:latest"]
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25")
    assert post_image(c, context=ctx(model=MOON)).status_code == 200


def _post_in_thread(c, results, context):
    t = threading.Thread(target=lambda: results.append(post_image(c, context=context)))
    t.start()
    return t


def test_ollama_switch_behind_request_in_flight_refused(oclient, fake):
    # Qwen in flight (15 s assumed) + moondream load (15 s) + answer (15 s)
    # = 45 s > 0.9 x 45 s, while another Qwen request (30 s) would be queued.
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="45", OLLAMA_QUEUE_SIZE="1")
    fake.release.clear()
    results: list = []
    t = _post_in_thread(c, results, ctx())
    assert _wait(lambda: len(fake.chats()) == 1)
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 503 and "model switch would time out" in r.json()["error"]["message"]
    assert "Retry-After" in r.headers
    fake.release.set()
    t.join(5)
    assert results[0].status_code == 200


def test_ollama_switch_behind_request_in_flight_admitted_when_it_fits(oclient, fake):
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="45", OLLAMA_QUEUE_SIZE="1")
    b = c.app.state.backend
    b._record(2.0)
    b._record(2.0, MOON)  # 2 + 15 (load floor) + 2 = 19 s fits 40.5 s
    fake.release.clear()
    results: list = []
    t1 = _post_in_thread(c, results, ctx())
    assert _wait(lambda: len(fake.chats()) == 1)
    t2 = _post_in_thread(c, results, ctx(model=MOON))
    assert _wait(lambda: b._waiting == 1)
    fake.release.set()
    t1.join(5)
    t2.join(5)
    assert sorted(r.json()["model_used"] for r in results) == [MOON, QWEN]
    assert [ch["model"] for ch in fake.chats()] == [QWEN, MOON]


def test_ollama_latency_is_tracked_per_model(oclient, fake):
    fake.loaded = [QWEN, "moondream:latest"]
    c = oclient(ALLOWED_MODELS=MOON)
    fake.chat_delay = 0.05
    assert post_image(c, context=ctx()).status_code == 200
    fake.chat_delay = 0.5
    assert post_image(c, context=ctx(model=MOON)).status_code == 200
    body = assert_contract(c.get("/v1/info"), "/v1/info")
    qwen, moon = body["models"]
    assert body["avg_latency_ms"] == qwen["avg_latency_ms"] < 400  # default's, as before
    assert moon["avg_latency_ms"] >= 450
    b = c.app.state.backend
    assert len(b.stats(QWEN).recent) == 1 and len(b.stats(MOON).recent) == 1


def test_ollama_model_without_measurement_has_no_latency(oclient, fake):
    c = oclient(ALLOWED_MODELS=MOON)
    c.app.state.backend._avg_s = 1.0
    qwen, moon = c.get("/v1/info").json()["models"]
    assert qwen["avg_latency_ms"] == 1000 and "avg_latency_ms" not in moon


def test_ollama_info_keeps_a_picked_model_loaded(oclient, fake):
    # Moondream (allowed, picked by a client) is loaded and Qwen is not:
    # still ready, and no reload of Qwen that would evict it again.
    fake.loaded = ["moondream:latest"]
    c = oclient(ALLOWED_MODELS=MOON)
    body = c.get("/v1/info").json()
    assert body["ready"] is True
    assert [m["loaded"] for m in body["models"]] == [False, True]
    time.sleep(0.2)
    assert fake.generates() == []


def test_ollama_info_reloads_default_when_no_allowed_model_is_loaded(oclient, fake):
    fake.loaded = ["llava:7b"]
    c = oclient(ALLOWED_MODELS=MOON)
    assert c.get("/v1/info").json()["ready"] is False
    assert _wait(lambda: [g["model"] for g in fake.generates()] == [QWEN])


def test_ollama_prompt_does_not_depend_on_model(oclient, fake):
    # The prompt and schema do not depend on context.model.
    c = oclient(ALLOWED_MODELS=MOON)
    post_image(c, context=ctx())
    a = fake.chats()[-1]
    fake.loaded = ["moondream:latest"]
    post_image(c, context=ctx(model=MOON))
    b = fake.chats()[-1]
    assert a["messages"] == b["messages"] and a["format"] == b["format"]
