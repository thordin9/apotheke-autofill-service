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


@pytest.mark.parametrize(
    "raw",
    ["672**", "+448", "4_48", "448,-672", "672 *", "*", "448*1", "\u0664\u0664\u0668", "0x1c0"],
)
def test_parse_image_sizes_strict_item_format(raw):
    # Each entry must match ^\d+\*?$ (ASCII digits, one optional trailing *).
    with pytest.raises(RuntimeError, match="must be integers"):
        parse_image_sizes(raw)


def test_parse_image_sizes_strict_still_takes_spaces_and_leading_zeros():
    assert parse_image_sizes(" 0448 , 672* ") == ((448, False), (672, True))


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
    # v1.3 adds three strings. Older clients ignore them; the scored fields stay.
    assert set(body) == {
        "name",
        "category",
        "tags",
        "description",
        "model_used",
        "brand",
        "model_number",
        "serial_number",
    }
    assert body["brand"] == body["model_number"] == body["serial_number"] == ""


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

    assert sanitize({"model_used": "m"}) == {
        "model_used": "m",
        "brand": "",
        "model_number": "",
        "serial_number": "",
    }
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
        {"id": QWEN, "default": True, "loaded": True, "state": "warm"},
        {"id": MOON, "default": False, "loaded": False, "state": "cold"},
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
        {"id": QWEN, "default": True, "loaded": True, "state": "loading"},
        {"id": MOON, "default": False, "loaded": False, "state": "cold"},
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
    # 15 s estimate + max(2 s, 30%) margin = 19.5 s, rounded up.
    assert r.status_code == 503 and r.headers["Retry-After"] == "20"
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


# -- follow-up: switch Retry-After (B2) and /v1/info during a switch (B3) -----


def test_ollama_switch_retry_after_uses_measured_load_over_15_s(oclient, fake):
    # Moondream last loaded in 22.4 s: its Retry-After is 30 (22.4 s + 30%
    # margin), not 15, so a single client retry does not land mid-load.
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25", OLLAMA_RETRY_AFTER_S="5")
    c.app.state.backend._record_load(22.4, MOON)
    fake.warm_delay = 0.3
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 503 and r.headers["Retry-After"] == "30"


def test_ollama_switch_retry_after_measured_load_under_15_s_keeps_floor(oclient, fake):
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25", OLLAMA_RETRY_AFTER_S="5")
    c.app.state.backend._record_load(4.0, MOON)
    fake.warm_delay = 0.3
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 503 and r.headers["Retry-After"] == "20"


def test_ollama_switch_retry_after_counts_down_while_loading(oclient, fake):
    # A retry while moondream's background load is running gets the time
    # left: 22.4 s estimate + 30% - 10 s already loading -> 20.
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25")
    b = c.app.state.backend
    b._record_load(22.4, MOON)
    fake.warm_delay = 2.0
    assert post_image(c, context=ctx(model=MOON)).headers["Retry-After"] == "30"
    model, started = b._loading
    assert model == MOON
    b._loading = (MOON, started - 10.0)
    assert post_image(c, context=ctx(model=MOON)).headers["Retry-After"] == "20"
    assert [g["model"] for g in fake.generates()] == [MOON]  # still one load


def test_ollama_switch_retry_after_is_at_least_1(oclient, fake):
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25")
    b = c.app.state.backend
    fake.warm_delay = 2.0
    assert post_image(c, context=ctx(model=MOON)).headers["Retry-After"] == "20"
    b._loading = (MOON, b._loading[1] - 60.0)  # overran its estimate
    assert post_image(c, context=ctx(model=MOON)).headers["Retry-After"] == "1"


def test_ollama_retry_after_ignores_a_load_of_another_model(oclient, fake):
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25")
    b = c.app.state.backend
    b._loading = (QWEN, time.monotonic() - 10.0)
    assert b._cold_retry_after(MOON) == 20


def test_ollama_loading_marker_cleared_after_load(oclient, fake):
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25")
    b = c.app.state.backend
    post_image(c, context=ctx(model=MOON))
    assert _wait(lambda: b._loading is None and not b._reload_thread.is_alive())


def test_ollama_info_during_loading_switch_does_not_reload_default(oclient, fake):
    # 25 s: the switch to moondream gets a fast 503 and loads in the
    # background. /api/ps does not list moondream until the load is done, so
    # no allowed model looks loaded; /v1/info must still not queue a Qwen
    # reload that would evict moondream right after.
    fake.loaded = []
    fake.warm_delay = 0.5
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25")
    assert post_image(c, context=ctx(model=MOON)).status_code == 503
    for _ in range(3):
        body = assert_contract(c.get("/v1/info"), "/v1/info")
        assert body["ready"] is False
    b = c.app.state.backend
    assert _wait(lambda: not b._reload_thread.is_alive())
    fake.loaded = ["moondream:latest"]
    body = c.get("/v1/info").json()
    assert body["ready"] is True
    time.sleep(0.2)
    assert [g["model"] for g in fake.generates()] == [MOON]


def test_ollama_info_during_waiting_switch_does_not_reload_default(oclient, fake):
    # 45 s: the switch request waits while its chat call loads moondream.
    # /v1/info polled meanwhile must not reload Qwen.
    fake.loaded = []
    fake.release.clear()
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="45")
    results: list = []
    t = _post_in_thread(c, results, ctx(model=MOON))
    assert _wait(lambda: len(fake.chats()) == 1)
    assert c.get("/v1/info").json()["ready"] is False
    time.sleep(0.2)
    assert fake.generates() == []
    fake.release.set()
    t.join(5)
    assert results[0].status_code == 200 and results[0].json()["model_used"] == MOON


def test_ollama_info_reloads_default_once_nothing_is_in_flight(oclient, fake):
    # The B3 guard only defers the reload: with no switch, load or request in
    # flight, an unloaded default is reloaded as before.
    fake.loaded = []
    c = oclient(ALLOWED_MODELS=MOON)
    b = c.app.state.backend
    b._in_flight = 1
    c.get("/v1/info")
    time.sleep(0.2)
    assert fake.generates() == []
    b._in_flight = 0
    assert c.get("/v1/info").json()["ready"] is False
    assert _wait(lambda: [g["model"] for g in fake.generates()] == [QWEN])


# -- follow-up: queued switch loads (B1), echo queue (E1), installed cache -----


def test_ollama_switch_while_another_reload_is_running(oclient, fake):
    # A background reload of Qwen is running when a client switches to
    # moondream: moondream is queued and loads right after, so the client's
    # retry finds it loading (B1). Loads stay one at a time. 40 s timeout
    # (Retry-After cap 45) with a slow moondream answer, so it is a fast 503.
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="40")
    b = c.app.state.backend
    b._record(30.0, MOON)
    fake.warm_delay = 0.6
    b._reload(QWEN)
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 503 and "moondream" in r.json()["error"]["message"]
    # Its own 19.5 s (15 s + margin) plus what is left of Qwen's 19.5 s.
    assert int(r.headers["Retry-After"]) in (38, 39)
    assert b._pending == MOON
    assert _wait(lambda: b._loading is not None and b._loading[0] == MOON)
    r = post_image(c, context=ctx(model=MOON))  # the retry: moondream is loading
    assert r.status_code == 503 and int(r.headers["Retry-After"]) <= 20
    assert _wait(lambda: not b._reload_active)
    assert [g["model"] for g in fake.generates()] == [QWEN, MOON]
    assert b._loading is None and b._pending is None


def test_ollama_queued_load_latest_request_wins(oclient, fake):
    c = oclient(ALLOWED_MODELS=f"{MOON},llava:7b", BACKEND_TIMEOUT_S="25")
    b = c.app.state.backend
    fake.warm_delay = 0.4
    b._reload(QWEN)
    assert post_image(c, context=ctx(model=MOON)).status_code == 503
    assert post_image(c, context=ctx(model="llava:7b")).status_code == 503
    assert _wait(lambda: not b._reload_active)
    assert [g["model"] for g in fake.generates()] == [QWEN, "llava:7b"]


def test_ollama_queued_load_of_the_running_model_is_dropped(oclient, fake):
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25")
    b = c.app.state.backend
    fake.warm_delay = 0.3
    b._reload(MOON)
    b._reload(MOON)
    assert b._pending is None
    assert _wait(lambda: not b._reload_active)
    assert [g["model"] for g in fake.generates()] == [MOON]


def test_ollama_queued_load_runs_after_a_failed_load(oclient, fake):
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25")
    b = c.app.state.backend
    fake.warm_delay = 0.3
    fake.warm_status = 500
    b._reload(QWEN)
    b._reload(MOON)
    assert _wait(lambda: not b._reload_active)
    assert [g["model"] for g in fake.generates()] == [QWEN, MOON]


def test_ollama_retry_after_for_a_queued_load(oclient, fake):
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="40")  # cap 45
    b = c.app.state.backend
    b._loading, b._pending = (QWEN, time.monotonic() - 5.0), MOON
    assert b._cold_retry_after(MOON) == 34  # 19.5 + (19.5 - 5)
    b._loading = (QWEN, time.monotonic() - 60.0)  # Qwen overran its estimate
    assert b._cold_retry_after(MOON) == 20


def test_ollama_install_shows_up_after_cache_ttl(oclient, fake, monkeypatch):
    import app.backends.ollama as ollama

    monkeypatch.setattr(ollama, "INSTALLED_TTL_S", 0.3)
    fake.tags = [QWEN]
    c = oclient(ALLOWED_MODELS=MOON)
    assert [m["id"] for m in c.get("/v1/info").json()["models"]] == [QWEN]
    fake.tags = [QWEN, "moondream:latest"]  # ollama pull moondream
    assert [m["id"] for m in c.get("/v1/info").json()["models"]] == [QWEN]  # cached
    assert post_image(c, context=ctx(model=MOON)).json()["model_used"] == QWEN
    time.sleep(0.35)
    assert [m["id"] for m in c.get("/v1/info").json()["models"]] == [QWEN, MOON]
    fake.loaded = ["moondream:latest"]
    assert post_image(c, context=ctx(model=MOON)).json()["model_used"] == MOON


def test_ollama_removal_shows_up_after_cache_ttl(oclient, fake, monkeypatch):
    import app.backends.ollama as ollama

    monkeypatch.setattr(ollama, "INSTALLED_TTL_S", 0.3)
    c = oclient(ALLOWED_MODELS=MOON)
    assert [m["id"] for m in c.get("/v1/info").json()["models"]] == [QWEN, MOON]
    fake.tags = [QWEN]  # ollama rm moondream
    assert [m["id"] for m in c.get("/v1/info").json()["models"]] == [QWEN, MOON]  # cached
    time.sleep(0.35)
    assert [m["id"] for m in c.get("/v1/info").json()["models"]] == [QWEN]
    assert post_image(c, context=ctx(model=MOON)).json()["model_used"] == QWEN


def test_echo_concurrent_switch_is_queued_when_it_fits(make_client):
    # Echo answers one request at a time: the moondream request waits for the
    # default one in flight, then pays its load (0.3 + 0.3 + 0.3 s).
    c = make_client(
        ALLOWED_MODELS=MOON, ECHO_DELAY_MS="300", ECHO_LOAD_MS="300", BACKEND_TIMEOUT_S="4.5"
    )
    b = c.app.state.backend
    results: list = []
    t0 = time.monotonic()
    t1 = _post_in_thread(c, results, ctx())
    assert _wait(lambda: b._in_flight == 1)
    t2 = _post_in_thread(c, results, ctx(model=MOON))
    t1.join(5)
    t2.join(5)
    assert [r.status_code for r in results] == [200, 200]
    assert [r.json()["model_used"] for r in results] == ["echo", MOON]
    assert time.monotonic() - t0 >= 0.85
    assert b._in_flight == 0 and b._waiting == 0


def test_echo_concurrent_switch_refused_when_it_would_time_out(make_client):
    # 1 s in flight + 1.5 s load + 1 s answer = 3.5 s > 0.9 x 3 s.
    c = make_client(
        ALLOWED_MODELS=MOON, ECHO_DELAY_MS="1000", ECHO_LOAD_MS="1500", BACKEND_TIMEOUT_S="3"
    )
    b = c.app.state.backend
    results: list = []
    t = _post_in_thread(c, results, ctx())
    assert _wait(lambda: b._in_flight == 1)
    r = post_image(c, context=ctx(model=MOON))
    body = assert_contract(r, "/v1/suggest", "post")
    assert r.status_code == 503 and "model switch would time out" in body["error"]["message"]
    assert r.headers["Retry-After"] == "3"  # 1 s ahead + 1.5 s switch load
    t.join(5)
    assert results[0].status_code == 200 and results[0].json()["model_used"] == "echo"


def test_echo_concurrent_same_model_is_queued(make_client):
    # Same model, no load: 1 s + 1 s = 2 s fits 0.9 x 3 s.
    c = make_client(
        ALLOWED_MODELS=MOON, ECHO_DELAY_MS="1000", ECHO_LOAD_MS="1500", BACKEND_TIMEOUT_S="3"
    )
    b = c.app.state.backend
    results: list = []
    t1 = _post_in_thread(c, results, ctx())
    assert _wait(lambda: b._in_flight == 1)
    t2 = _post_in_thread(c, results, ctx())
    t1.join(5)
    t2.join(5)
    assert [r.status_code for r in results] == [200, 200]


def test_echo_queue_full(make_client):
    c = make_client(ALLOWED_MODELS=MOON, ECHO_DELAY_MS="600", BACKEND_TIMEOUT_S="10")
    b = c.app.state.backend
    results: list = []
    t1 = _post_in_thread(c, results, ctx())
    assert _wait(lambda: b._in_flight == 1)
    t2 = _post_in_thread(c, results, ctx(model=MOON))
    assert _wait(lambda: b._waiting == 1)
    r = post_image(c, context=ctx())
    assert r.status_code == 503 and "queue full" in r.json()["error"]["message"]
    assert r.headers["Retry-After"] == "2"  # 0.6 s x 2, rounded up
    t1.join(5)
    t2.join(5)
    assert [x.status_code for x in results] == [200, 200]


def test_echo_slot_released_after_a_failure(make_client, monkeypatch):
    c = make_client(ALLOWED_MODELS=MOON, ECHO_MODE="error")
    assert post_image(c, context=ctx()).status_code == 503
    assert c.app.state.backend._in_flight == 0
    monkeypatch.setenv("ECHO_MODE", "ok")
    assert post_image(c, context=ctx(model=MOON)).status_code == 200


def test_echo_without_allowed_models_is_not_serialized(make_client):
    # v1 echo is unchanged: concurrent requests run side by side.
    c = make_client(ECHO_DELAY_MS="500")
    results: list = []
    t0 = time.monotonic()
    threads = [_post_in_thread(c, results, ctx()) for _ in range(3)]
    for t in threads:
        t.join(5)
    assert [r.status_code for r in results] == [200, 200, 200]
    assert time.monotonic() - t0 < 1.4


# -- follow-up: QA of #11 (B3b, B2 margin) -------------------------------------


def test_b3_toctou_ps_snapshot_before_load_finishes(oclient, fake):
    # QA B3b: /api/ps answers with a snapshot taken before moondream's
    # background load finished (so it lists nothing), and the answer arrives
    # after the load is done. /v1/info must not reload the default on that
    # stale snapshot: it would evict the model that just loaded.
    fake.loaded = []
    fake.warm_delay = 0.2
    fake.ps_delay = 0.5
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25")
    b = c.app.state.backend
    b._reload(MOON)
    assert c.get("/v1/info").json()["ready"] is False
    assert not b._reload_active  # the load finished during the /api/ps call
    time.sleep(0.3)
    assert [g["model"] for g in fake.generates()] == [MOON]


def test_b3_request_finishing_during_ps_call_skips_reload(oclient, fake):
    # The same window for a request: one that starts and ends while /api/ps
    # is answering is seen through the activity counter.
    fake.loaded = []
    fake.ps_delay = 0.4
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="45")
    b = c.app.state.backend
    results: list = []
    t = threading.Thread(target=lambda: results.append(c.get("/v1/info")))
    t.start()
    time.sleep(0.1)
    b._activity += 2  # a request started and finished meanwhile
    t.join(5)
    time.sleep(0.2)
    assert results[0].json()["ready"] is False and fake.generates() == []


def test_b3_guard_applies_without_allowed_models(oclient, fake):
    # Without ALLOWED_MODELS the default reload also waits while a request
    # is in flight, then happens once nothing is.
    fake.loaded = []
    c = oclient()
    b = c.app.state.backend
    b._in_flight = 1
    assert c.get("/v1/info").json()["ready"] is False
    time.sleep(0.2)
    assert fake.generates() == []
    b._in_flight = 0
    c.get("/v1/info")
    assert _wait(lambda: [g["model"] for g in fake.generates()] == [QWEN])


@pytest.mark.parametrize("last_load_s", [10.0, 15.0, 22.4, 40.0])
def test_ollama_switch_retry_after_covers_a_load_30_percent_slower(oclient, last_load_s):
    # QA B2 note: the next load can be slower than the last one. Retry-After
    # covers a load 30% slower than the estimate (max(15 s, last load)), as
    # long as that stays under the cap (BACKEND_TIMEOUT_S + 5 = 125 here).
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="120")
    b = c.app.state.backend
    b._record_load(last_load_s, MOON)
    slower = 1.3 * max(15.0, last_load_s)
    assert b._cold_retry_after(MOON) >= slower
    assert b._cold_retry_after(MOON) - slower < 1  # and not much more


def test_ollama_switch_retry_after_margin_is_at_least_2_s(oclient, monkeypatch):
    import app.backends.ollama as ollama

    monkeypatch.setattr(ollama, "LOAD_MARGIN_SHARE", 0.05)
    c = oclient(ALLOWED_MODELS=MOON)
    assert c.app.state.backend._cold_retry_after(MOON) == 17  # 15 + max(2, 0.75)


def test_ollama_switch_retry_after_capped_at_client_timeout(oclient, fake):
    # 40 s measured load: 40 + 30% = 52 s, capped at BACKEND_TIMEOUT_S + 5 =
    # 50 (the recommended client timeout), so Apotheke still retries.
    fake.warm_delay = 0.3
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="45")
    b = c.app.state.backend
    b._record_load(40.0, MOON)
    b._record(30.0, MOON)  # 40 + 30 > 0.9 x 45: fast 503
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 503 and r.headers["Retry-After"] == "50"


def test_ollama_switch_retry_after_cap_follows_backend_timeout(oclient):
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25")
    b = c.app.state.backend
    b._record_load(40.0, MOON)
    assert b._cold_retry_after(MOON) == 30
    b._record_load(15.0, MOON)
    assert b._cold_retry_after(MOON) == 20  # under the cap: unchanged


def test_ollama_queued_switch_retry_after_is_capped(oclient, fake):
    # B1 queued value: 19.5 s own budget + up to 19.5 s left of the running
    # load = 39, capped at 25 + 5 = 30.
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="25")
    b = c.app.state.backend
    fake.warm_delay = 0.6
    b._reload(QWEN)
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 503 and r.headers["Retry-After"] == "30"
    assert b._pending == MOON
    b._loading, b._pending = (QWEN, time.monotonic() - 5.0), MOON
    assert b._cold_retry_after(MOON) == 30  # 34 uncapped
    assert _wait(lambda: not b._reload_active)


def _states(body):
    return {m["id"]: m for m in body["models"]}


def test_state_is_loading_while_resident_until_a_load_or_suggest_finishes(oclient, fake):
    # F4: /api/ps lists the model while it is still loading. With warm-up off
    # and no background load of our own, loaded may be true and state stays
    # loading until a suggest for that model finishes.
    fake.loaded = [QWEN]
    fake.release.clear()
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="45")
    before = _states(c.get("/v1/info").json())
    assert before[QWEN]["loaded"] is True and before[QWEN]["state"] == "loading"
    assert before[MOON]["state"] == "cold" and before[MOON]["loaded"] is False
    assert c.get("/healthz").json() == {"status": "ok"}
    results: list = []
    t = _post_in_thread(c, results, ctx())
    assert _wait(lambda: len(fake.chats()) == 1)
    mid = _states(c.get("/v1/info").json())
    assert mid[QWEN]["loaded"] is True and mid[QWEN]["state"] == "loading"
    assert "models" not in c.get("/healthz").json()
    fake.release.set()
    t.join(5)
    assert results[0].status_code == 200
    warm = _states(c.get("/v1/info").json())
    assert warm[QWEN]["state"] == "warm" and warm[QWEN]["loaded"] is True
    fake.loaded = []
    assert _states(c.get("/v1/info").json())[QWEN]["state"] == "cold"
    # That read starts a background reload. Once it finishes, the model is warm
    # when /api/ps lists it again, without another suggest.
    b = c.app.state.backend
    assert _wait(lambda: model_key(QWEN) in b._succeeded and b._loading is None)
    fake.loaded = [QWEN]
    again = _states(c.get("/v1/info").json())[QWEN]
    assert again["loaded"] is True and again["state"] == "warm"


def test_size_vram_zero_degrades_healthz_for_the_production_model(oclient, fake):
    fake.loaded = [QWEN]
    fake.size_vram = 0
    c = oclient(ALLOWED_MODELS=MOON)
    info = c.get("/v1/info").json()
    assert _states(info)[QWEN]["size_vram"] == 0
    assert _states(info)[QWEN]["loaded"] is True
    assert _states(info)[QWEN]["state"] == "loading"
    health = c.get("/healthz")
    assert health.status_code == 503
    assert assert_contract(health, "/healthz")["status"] == "degraded"
    assert health.json() == {"status": "degraded"}
    fake.size_vram = 4_000_000_000
    health = c.get("/healthz")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    assert _states(c.get("/v1/info").json())[QWEN]["size_vram"] == 4_000_000_000
    assert post_image(c).status_code == 200
    assert _states(c.get("/v1/info").json())[QWEN]["state"] == "warm"
    assert c.get("/healthz").status_code == 200


def test_size_vram_zero_on_a_non_production_model_degrades_only_once_warm(oclient, fake):
    fake.loaded = ["moondream:latest"]
    fake.size_vram = 0
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="45")
    assert c.get("/healthz").status_code == 200
    assert _states(c.get("/v1/info").json())[MOON]["state"] == "loading"
    assert post_image(c, context=ctx(model=MOON)).status_code == 200
    assert _states(c.get("/v1/info").json())[MOON]["state"] == "warm"
    health = c.get("/healthz")
    assert health.status_code == 503 and health.json()["status"] == "degraded"


def test_f5_mixed_cold_sequence_refuses_the_trailing_request(oclient, fake):
    # QA: nothing loaded, default reload, a switch in flight, then the default
    # queued behind it. Measured times fit 0.9 x 45 s only if the in-flight
    # load and the background reload are ignored — that path used to 504.
    fake.loaded = []
    fake.warm_delay = 3.0
    fake.release.clear()
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="45", OLLAMA_QUEUE_SIZE="1")
    b = c.app.state.backend
    b._record(8.0)
    b._record(8.0, MOON)
    assert c.get("/v1/info").json()["ready"] is False
    assert b._loading is not None and b._loading[0] == QWEN
    results: list = []
    t = _post_in_thread(c, results, ctx(model=MOON))
    assert _wait(lambda: b._in_flight == 1 and b._inflight_load is not None)
    assert b._unfinished_load_s() > 20  # background qwen + in-flight moondream
    r = post_image(c, context=ctx())
    assert r.status_code == 503
    assert "would time out" in r.json()["error"]["message"]
    assert int(r.headers["Retry-After"]) >= 20
    assert b._waiting == 0 and len(fake.chats()) == 1
    fake.release.set()
    t.join(5)
    assert results[0].status_code == 200


def test_f5_echo_counts_the_inflight_load(make_client):
    c = make_client(
        ALLOWED_MODELS=MOON, ECHO_DELAY_MS="400", ECHO_LOAD_MS="4000", BACKEND_TIMEOUT_S="6"
    )
    b = c.app.state.backend
    results: list = []
    t = _post_in_thread(c, results, ctx(model=MOON))
    assert _wait(lambda: b._in_flight == 1 and b._ready_at > time.monotonic() + 2)
    r = post_image(c, context=ctx())
    assert r.status_code == 503 and "would time out" in r.json()["error"]["message"]
    assert int(r.headers["Retry-After"]) >= 5  # 0.4 s ahead + 4 s switch load
    t.join(8)
    assert results[0].status_code == 200


def test_f6_queue_full_retry_after_includes_switch_load(oclient, fake):
    c = oclient(
        ALLOWED_MODELS=MOON,
        BACKEND_TIMEOUT_S="60",
        OLLAMA_QUEUE_SIZE="1",
        OLLAMA_RETRY_AFTER_S="1",
    )
    b = c.app.state.backend
    b._record(10.0)
    fake.loaded = [QWEN]
    fake.release.clear()
    results: list = []
    t1 = _post_in_thread(c, results, ctx())
    assert _wait(lambda: b._in_flight == 1)
    t2 = _post_in_thread(c, results, ctx())
    assert _wait(lambda: b._waiting == 1)
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 503 and "queue full" in r.json()["error"]["message"]
    # avg 10 s x (waiting + 1) + load budget 15 s + max(2 s, 30%) = 39.5 -> 40
    assert r.headers["Retry-After"] == "40"
    fake.release.set()
    t1.join(5)
    t2.join(5)


def test_f6_echo_queue_full_includes_switch_load(make_client):
    c = make_client(
        ALLOWED_MODELS=MOON, ECHO_DELAY_MS="600", ECHO_LOAD_MS="2500", BACKEND_TIMEOUT_S="30"
    )
    b = c.app.state.backend
    results: list = []
    t1 = _post_in_thread(c, results, ctx())
    assert _wait(lambda: b._in_flight == 1)
    t2 = _post_in_thread(c, results, ctx())
    assert _wait(lambda: b._waiting == 1)
    r = post_image(c, context=ctx(model=MOON))
    assert r.status_code == 503 and "queue full" in r.json()["error"]["message"]
    # 0.6 s x 2 + 2.5 s load = 3.7 → 4
    assert r.headers["Retry-After"] == "4"
    t1.join(5)
    t2.join(5)


def test_f6_queue_full_retry_after_is_capped(oclient, fake):
    # avg 20 s x 2 + budget(22.4 s) 29.12 = 69.12 -> capped at BACKEND_TIMEOUT_S + 5.
    c = oclient(ALLOWED_MODELS=MOON, BACKEND_TIMEOUT_S="45", OLLAMA_QUEUE_SIZE="1")
    b = c.app.state.backend
    b._record(20.0)
    b._record_load(22.4, MOON)
    fake.release.clear()
    results: list = []
    t1 = _post_in_thread(c, results, ctx())
    assert _wait(lambda: b._in_flight == 1)
    t2 = _post_in_thread(c, results, ctx())
    assert _wait(lambda: b._waiting == 1)
    r = post_image(c, context=ctx(model=MOON))
    fake.release.set()
    t1.join(5)
    t2.join(5)
    assert r.status_code == 503 and "queue full" in r.json()["error"]["message"]
    assert r.headers["Retry-After"] == "50"


def test_healthz_does_not_list_models(oclient):
    c = oclient(ALLOWED_MODELS=MOON, AUTOFILL_TOKEN="secret")
    assert c.get("/healthz").json() == {"status": "ok"}
    assert c.get("/v1/info").status_code == 401


def test_warmup_marks_a_resident_model_warm_without_a_suggest(oclient, fake):
    fake.warm_delay = 0.2
    c = oclient(ALLOWED_MODELS=MOON, OLLAMA_WARMUP="1")
    b = c.app.state.backend
    b.start()
    assert b.ready is False
    assert _wait(lambda: b.ready)
    entry = _states(c.get("/v1/info").json())[QWEN]
    assert entry["loaded"] is True and entry["state"] == "warm"
    assert fake.chats() == []


def test_background_reload_marks_the_model_warm_when_it_finishes(oclient, fake):
    fake.loaded = []
    fake.track_loads = True
    fake.warm_delay = 0.4
    c = oclient(ALLOWED_MODELS=MOON)
    b = c.app.state.backend
    assert c.get("/v1/info").json()["ready"] is False
    assert _wait(lambda: b._loading is not None)
    assert _wait(lambda: _states(c.get("/v1/info").json())[QWEN]["loaded"] is True)
    assert _states(c.get("/v1/info").json())[QWEN]["state"] == "loading"
    assert _wait(lambda: model_key(QWEN) in b._succeeded and b._loading is None)
    done = _states(c.get("/v1/info").json())[QWEN]
    assert done["state"] == "warm" and done["loaded"] is True
    assert fake.chats() == []
