"""Neuron budget, repair, and deadline-aware Cloudflare-primary failover."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from conftest import GIF, assert_contract, post_image
from ollama_mock import FakeOllama
from test_cloudflare import (
    ACCOUNT,
    MODEL,
    PHOTO,
    TOKEN,
    VALID,
    _body,
    _cf_env,
    _ok,
    _paid,
    _secrets_absent,
)

from app.backends.budget import (
    GEMMA_MODEL,
    MISTRAL_MODEL,
    NeuronBudget,
    estimate_neurons,
    neurons_from_usage,
)
from app.backends.cloudflare import (
    BUDGET_USED_UP,
    CloudflareBackend,
    _strip_fence,
)
from app.backends.failover import (
    BOTH_FAILED,
    LOCAL_DEADLINE,
    NOT_ENOUGH_TIME,
    effective_cf_slice_s,
    repair_allowance_s,
)
from app.backends.selection import model_key
from app.settings import get_settings

BENCH = Path(__file__).parents[1] / "bench" / "results" / "cf-workers-ai-2026-10-19" / "raw"


@pytest.fixture
def fake():
    server = FakeOllama()
    yield server
    server.close()


@pytest.fixture
def oclient(make_client, fake):
    def _make(**env):
        env.setdefault("OLLAMA_URL", fake.url)
        env.setdefault("OLLAMA_WARMUP", "0")
        return make_client(BACKEND="ollama", **env)

    return _make


@pytest.fixture
def cf_route():
    with respx.mock(assert_all_called=False) as router:
        router.route(host="127.0.0.1").pass_through()
        router.route(host="testserver").pass_through()
        router.route(host="localhost").pass_through()
        yield router.post(host="api.cloudflare.com")


def _primary(fake: FakeOllama, **extra: str) -> dict[str, str]:
    env = {
        "BACKEND": "cloudflare",
        "FALLBACK_BACKEND": "ollama",
        "OLLAMA_URL": fake.url,
        "OLLAMA_WARMUP": "0",
        "BACKEND_TIMEOUT_S": "45",
        "REQUEST_DEADLINE_S": "45",
        "OLLAMA_WARM_RESERVE_S": "15",
        "OLLAMA_COLD_RESERVE_S": "35",
        **_cf_env(CF_TIMEOUT_S="20"),
    }
    env.update(extra)
    return env


class _Clock:
    def __init__(self, start: float = 1_000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t


# -- slice math ---------------------------------------------------------------


def test_warm_slice_is_the_full_timeout_and_cold_slice_is_about_10s():
    assert repair_allowance_s(45, 20, 15) == 0
    assert repair_allowance_s(45, 20, 35) == 0
    assert effective_cf_slice_s(45, 20, 15) == 20
    assert effective_cf_slice_s(45, 20, 35) == 10


def test_startup_rejects_a_deadline_that_is_not_under_50s(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CF_API_TOKEN", TOKEN)
    monkeypatch.setenv("FALLBACK_BACKEND", "ollama")
    monkeypatch.setenv("REQUEST_DEADLINE_S", "50")
    monkeypatch.setenv("BACKEND_TIMEOUT_S", "60")
    with pytest.raises(RuntimeError, match="50 s"):
        from app.backends import create_backend

        create_backend("cloudflare")


def test_startup_rejects_a_cold_reserve_that_leaves_no_cloudflare_slice(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CF_API_TOKEN", TOKEN)
    monkeypatch.setenv("FALLBACK_BACKEND", "ollama")
    monkeypatch.setenv("REQUEST_DEADLINE_S", "45")
    monkeypatch.setenv("BACKEND_TIMEOUT_S", "45")
    monkeypatch.setenv("OLLAMA_COLD_RESERVE_S", "41")
    with pytest.raises(RuntimeError, match="OLLAMA_COLD_RESERVE_S"):
        from app.backends import create_backend

        create_backend("cloudflare")


def test_startup_rejects_a_deadline_longer_than_the_service_timeout(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CF_API_TOKEN", TOKEN)
    monkeypatch.setenv("FALLBACK_BACKEND", "ollama")
    monkeypatch.setenv("REQUEST_DEADLINE_S", "45")
    monkeypatch.setenv("BACKEND_TIMEOUT_S", "25")
    with pytest.raises(RuntimeError, match="BACKEND_TIMEOUT_S"):
        from app.backends import create_backend

        create_backend("cloudflare")


# -- budget file --------------------------------------------------------------


def test_missing_file_starts_at_zero_when_create_succeeds(tmp_path):
    path = tmp_path / "fresh" / "cf-neuron-budget.json"
    budget = NeuronBudget(str(path), 6000, GEMMA_MODEL)
    assert budget.used == 0
    assert budget.can_call()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"day": saved["day"], "used": 0}
    assert set(saved) == {"day", "used"}
    assert ACCOUNT not in path.read_text(encoding="utf-8")


def test_missing_file_is_used_up_when_create_fails(tmp_path, caplog):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        budget = NeuronBudget(str(blocker / "cf.json"), 6000, GEMMA_MODEL)
    view = budget.view()
    assert view.remaining == 0
    assert view.can_call is False
    assert view.reason is not None and "could not be created" in view.reason
    assert "cloudflare budget unavailable" in caplog.text
    _secrets_absent(caplog.text)


def test_corrupt_file_is_used_up_and_not_rewritten(tmp_path, caplog):
    path = tmp_path / "cf.json"
    path.write_text("{not-json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        budget = NeuronBudget(str(path), 6000, GEMMA_MODEL)
    assert path.read_text(encoding="utf-8") == "{not-json"
    view = budget.view()
    assert view.remaining == 0 and view.can_call is False
    assert view.reason is not None and "corrupt" in view.reason
    assert "budget state file is corrupt" in caplog.text
    _secrets_absent(caplog.text)


def test_unreadable_file_is_used_up(tmp_path, monkeypatch, caplog):
    path = tmp_path / "cf.json"
    path.write_text('{"day":"2026-10-19","used":1}', encoding="utf-8")
    real = Path.read_text

    def boom(self: Path, *args, **kwargs):
        if self == path:
            raise OSError("denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    with caplog.at_level(logging.WARNING):
        budget = NeuronBudget(str(path), 6000, GEMMA_MODEL)
    assert budget.view().remaining == 0
    assert "unreadable" in (budget.view().reason or "")
    assert "denied" not in caplog.text
    _secrets_absent(caplog.text)


def test_corrupt_file_does_not_reset_on_a_new_utc_day(tmp_path, monkeypatch):
    path = tmp_path / "cf.json"
    path.write_text("nope", encoding="utf-8")
    monkeypatch.setattr(
        "app.backends.budget.utc_now",
        lambda: datetime(2026, 10, 20, 0, 1, tzinfo=UTC),
    )
    budget = NeuronBudget(str(path), 6000, GEMMA_MODEL)
    assert path.read_text(encoding="utf-8") == "nope"
    assert budget.can_call() is False


def test_readable_file_resets_on_a_new_utc_day(tmp_path, monkeypatch):
    path = tmp_path / "cf.json"
    path.write_text(json.dumps({"day": "2026-10-18", "used": 500}), encoding="utf-8")
    monkeypatch.setattr(
        "app.backends.budget.utc_now",
        lambda: datetime(2026, 10, 19, 3, 0, tzinfo=UTC),
    )
    budget = NeuronBudget(str(path), 6000, GEMMA_MODEL)
    assert budget.used == 0
    assert budget.can_call()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["day"] == "2026-10-19"
    assert saved["used"] == 0


def _mean_neurons(name: str, model: str, *, drop_neurons: bool) -> float:
    values: list[float] = []
    for line in (BENCH / name).read_text(encoding="utf-8").splitlines():
        usage = json.loads(line).get("usage_first")
        if not isinstance(usage, dict) or usage.get("prompt_tokens") is None:
            continue
        if drop_neurons:
            usage = {key: value for key, value in usage.items() if key != "neurons"}
        neurons, estimated = neurons_from_usage(usage, model)
        assert estimated is False
        values.append(neurons)
    assert values
    return sum(values) / len(values)


def test_bench_token_rates_match_measured_neurons():
    gemma = _mean_neurons("cf-gemma4-26b-nothink-560.jsonl", GEMMA_MODEL, drop_neurons=True)
    mistral = _mean_neurons("cf-mistral-small-3.1-560.jsonl", MISTRAL_MODEL, drop_neurons=True)
    assert abs(gemma - 6.4) < 0.15
    assert abs(mistral - 19.4) < 0.15
    reported = _mean_neurons("cf-gemma4-26b-nothink-560.jsonl", GEMMA_MODEL, drop_neurons=False)
    assert abs(reported - 6.4) < 0.15


def test_reported_neurons_win_over_the_rate_table():
    usage = {"prompt_tokens": 1, "completion_tokens": 1, "neurons": 6.4}
    assert neurons_from_usage(usage, GEMMA_MODEL) == (6.4, False)
    tokens_only = {"prompt_tokens": 440, "completion_tokens": 100}
    charged, estimated = neurons_from_usage(tokens_only, GEMMA_MODEL)
    assert estimated is False and charged != 6.4
    fallback, was_estimate = neurons_from_usage({}, GEMMA_MODEL)
    assert was_estimate is True
    assert fallback == estimate_neurons(GEMMA_MODEL)


def test_no_usage_charges_the_estimate_and_408_is_transient(make_client, cf_route, caplog):
    cf_route.return_value = httpx.Response(408, json={"success": False, "errors": [{"code": 3046}]})
    client = make_client(BACKEND="cloudflare", **_cf_env())
    with caplog.at_level(logging.DEBUG):
        response = post_image(client, data=PHOTO)
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "backend_timeout"
    backend = client.app.state.backend
    assert backend.transient_failures == 1
    assert abs(backend.budget.view().used - estimate_neurons(MODEL)) < 0.01
    assert "cloudflare usage estimated" in caplog.text
    _secrets_absent(caplog.text)


def test_failed_write_after_a_call_spends_the_rest_of_the_day(
    make_client, cf_route, monkeypatch, caplog
):
    cf_route.return_value = _ok(usage={"prompt_tokens": 440, "completion_tokens": 94})
    client = make_client(BACKEND="cloudflare", **_cf_env())
    import os

    real = os.replace
    calls = {"n": 0}

    def boom(src, dst):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("disk")
        return real(src, dst)

    monkeypatch.setattr("app.backends.budget.os.replace", boom)
    with caplog.at_level(logging.WARNING):
        first = post_image(client, data=PHOTO)
    assert first.status_code == 200
    assert cf_route.call_count == 1
    assert "could not be written after a call" in caplog.text
    _secrets_absent(caplog.text)
    info = client.get("/v1/info").json()
    budget = info["backends"]["primary"]["budget"]
    assert budget["remaining"] == 0
    assert "could not be written" in budget["reason"]
    second = post_image(client, data=PHOTO)
    assert second.status_code == 503
    assert second.json()["error"]["message"] == BUDGET_USED_UP
    assert cf_route.call_count == 1


def test_primary_budget_exhausted_is_503_with_retry_after(make_client, cf_route):
    client = make_client(BACKEND="cloudflare", CF_DAILY_NEURON_BUDGET="1", **_cf_env())
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "backend_unavailable"
    assert body["error"]["message"] == BUDGET_USED_UP
    retry = int(response.headers["retry-after"])
    assert 1 <= retry <= 86400
    assert cf_route.call_count == 0
    info = assert_contract(client.get("/v1/info"), "/v1/info")
    budget = info["backends"]["primary"]["budget"]
    # Remaining is the real leftover. A limit of 1 cannot pay the estimate, so
    # the call is refused and the reason says the budget is used up.
    assert budget["remaining"] == 1
    assert budget["remaining"] < estimate_neurons(MODEL)
    assert budget["limit"] == 1
    assert "used up" in budget["reason"]
    assert info["backends"]["primary"]["state"] == "unavailable"


def test_budget_zero_disables_cloudflare_without_credentials(make_client, cf_route):
    client = make_client(BACKEND="cloudflare", CF_DAILY_NEURON_BUDGET="0")
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert response.json()["error"]["message"] == BUDGET_USED_UP
    assert cf_route.call_count == 0


def test_fallback_budget_keeps_the_request_local(oclient, cf_route):
    from ollama_mock import FakeOllama  # noqa: F401 — fixture provides the server

    client = oclient(FALLBACK_BACKEND="cloudflare", CF_DAILY_NEURON_BUDGET="1", **_cf_env())
    response = post_image(client, data=PHOTO)
    assert response.status_code == 200
    assert response.json()["backend_used"] == "ollama"
    assert cf_route.call_count == 0


def test_fallback_budget_does_not_hide_an_ollama_failure(oclient, fake, cf_route):
    fake.chat_status = 500
    client = oclient(FALLBACK_BACKEND="cloudflare", CF_DAILY_NEURON_BUDGET="1", **_cf_env())
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert response.json()["error"]["message"] != BOTH_FAILED
    assert "ollama returned HTTP 500" in response.json()["error"]["message"]
    assert cf_route.call_count == 0


def test_usage_persists_across_a_new_process(make_client, cf_route, tmp_path):
    path = tmp_path / "persist.json"
    usage = {"prompt_tokens": 440, "completion_tokens": 100}
    cf_route.return_value = _ok(usage=usage)
    first = make_client(BACKEND="cloudflare", CF_BUDGET_STATE_FILE=str(path), **_cf_env())
    assert post_image(first, data=PHOTO).status_code == 200
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["used"] > 0
    second = make_client(BACKEND="cloudflare", CF_BUDGET_STATE_FILE=str(path), **_cf_env())
    remaining = second.get("/v1/info").json()["backends"]["primary"]["budget"]["remaining"]
    assert remaining < 6000
    assert abs((6000 - remaining) - saved["used"]) < 0.01


def test_utc_midnight_resets_a_live_counter(make_client, cf_route, monkeypatch):
    cf_route.return_value = _ok(usage={"prompt_tokens": 440, "completion_tokens": 100})
    monkeypatch.setattr(
        "app.backends.budget.utc_now",
        lambda: datetime(2026, 10, 19, 23, 0, tzinfo=UTC),
    )
    client = make_client(BACKEND="cloudflare", **_cf_env())
    assert post_image(client, data=PHOTO).status_code == 200
    assert client.app.state.backend.budget.view().used > 0
    monkeypatch.setattr(
        "app.backends.budget.utc_now",
        lambda: datetime(2026, 10, 20, 0, 5, tzinfo=UTC),
    )
    view = client.app.state.backend.budget.view()
    assert view.used == 0
    assert view.can_call is True


def test_one_reservation_allows_only_one_parallel_call(monkeypatch, tmp_path):
    monkeypatch.setenv("CF_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CF_API_TOKEN", TOKEN)
    monkeypatch.setenv("CF_DAILY_NEURON_BUDGET", "50")
    monkeypatch.setenv("CF_BUDGET_STATE_FILE", str(tmp_path / "cf.json"))
    backend = CloudflareBackend()
    release = asyncio.Event()
    entered = asyncio.Event()
    posts = {"n": 0}
    original = httpx.AsyncClient.post

    async def post(self, url, *args, **kwargs):
        if "api.cloudflare.com" in str(url):
            posts["n"] += 1
            if posts["n"] == 1:
                entered.set()
                await release.wait()
            return _ok(usage={"prompt_tokens": 440, "completion_tokens": 94})
        return await original(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    async def run():
        first = asyncio.create_task(backend.suggest(PHOTO, "image/jpeg", {}))
        await entered.wait()
        second = asyncio.create_task(backend.suggest(PHOTO, "image/jpeg", {}))
        await asyncio.sleep(0.05)
        release.set()
        return await asyncio.gather(first, second, return_exceptions=True)

    results = asyncio.run(run())
    assert posts["n"] == 1
    successes = [item for item in results if isinstance(item, dict)]
    failures = [item for item in results if isinstance(item, BaseException)]
    assert len(successes) == 1 and successes[0]["backend_used"] == "cloudflare"
    assert len(failures) == 1
    assert isinstance(failures[0], Exception)
    assert BUDGET_USED_UP in str(failures[0])


def test_repair_is_a_second_charge(make_client, cf_route):
    usage = {"prompt_tokens": 100, "completion_tokens": 10}
    cf_route.side_effect = [_ok("???"), _ok(usage=usage)]
    client = make_client(BACKEND="cloudflare", **_cf_env(CF_MODEL="@cf/example/unlisted-vision"))
    assert post_image(client, data=PHOTO).status_code == 200
    assert cf_route.call_count == 2
    used = client.app.state.backend.budget.view().used
    one = neurons_from_usage(usage, "@cf/example/unlisted-vision")[0]
    # The unusable reply had no usage, so it costs the estimate. The repair costs `one`.
    assert used > one
    assert abs(used - (estimate_neurons("@cf/example/unlisted-vision") + one)) < 0.05


def test_fenced_json_is_accepted(make_client, cf_route):
    payload = "```json\n" + json.dumps(VALID) + "\n```"
    cf_route.return_value = _ok(payload)
    client = make_client(BACKEND="cloudflare", **_cf_env())
    body = post_image(client, data=PHOTO).json()
    assert body["backend_used"] == "cloudflare"
    assert body["name"]["value"] == "Cordless drill"
    assert _strip_fence(payload).startswith("{")


def test_empty_reply_repairs_once_then_is_unusable(make_client, cf_route):
    empty = httpx.Response(200, json={"success": True, "result": {"response": "  "}})
    cf_route.side_effect = [empty, empty]
    client = make_client(BACKEND="cloudflare", **_cf_env())
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_error"
    assert cf_route.call_count == 2


def test_length_cutoff_repairs_once(make_client, cf_route):
    cut = httpx.Response(
        200,
        json={
            "success": True,
            "result": {
                "response": "",
                "finish_reason": "length",
                "usage": {"prompt_tokens": 440, "completion_tokens": 1024},
            },
        },
    )
    cf_route.side_effect = [cut, _ok()]
    client = make_client(BACKEND="cloudflare", **_cf_env())
    assert post_image(client, data=PHOTO).status_code == 200
    assert cf_route.call_count == 2


def test_truncated_valid_json_is_kept_when_repair_does_not_fit(make_client, cf_route, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr("app.backends.cloudflare.time.monotonic", clock)

    def answer(_request):
        clock.t += 100
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "response": json.dumps(VALID),
                    "finish_reason": "length",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1024},
                },
            },
        )

    cf_route.side_effect = answer
    client = make_client(BACKEND="cloudflare", **_cf_env())
    assert post_image(client, data=PHOTO).status_code == 200
    assert cf_route.call_count == 1


# -- Cloudflare primary, Ollama fallback --------------------------------------


def test_info_shows_the_cold_and_warm_slice(make_client, fake):
    fake.loaded = []
    client = make_client(**_primary(fake))
    cold = assert_contract(client.get("/v1/info"), "/v1/info")
    primary = cold["backends"]["primary"]
    assert primary["cf_slice_s"] == 10
    assert primary["ollama_reserve_s"] == 35
    assert primary["ollama_state"] == "cold"
    assert primary["request_deadline_s"] == 45
    assert primary["budget"]["limit"] == 6000
    assert "token" not in json.dumps(cold)
    _secrets_absent(json.dumps(cold))
    ollama = client.app.state.backend.fallback
    ollama._succeeded.add(model_key(ollama.model))
    fake.loaded = ["moondream:latest"]
    fake.size_vram = 4096
    warm = client.get("/v1/info").json()["backends"]["primary"]
    assert warm["cf_slice_s"] == 20
    assert warm["ollama_reserve_s"] == 15
    assert warm["ollama_state"] == "warm"


def test_cf_primary_serves_when_cloudflare_answers(make_client, fake, cf_route):
    cf_route.return_value = _ok()
    client = make_client(**_primary(fake))
    body = assert_contract(post_image(client, data=PHOTO), "/v1/suggest", "post")
    assert body["backend_used"] == "cloudflare"
    assert body["model_used"] == MODEL
    assert fake.chats() == []
    payload = _body(cf_route.calls.last)
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize(
    "how",
    [
        "unreachable",
        "timeout",
        "http5",
        "http408",
        "http429",
        "http401",
        "http403",
        "paid",
        "malformed",
    ],
)
def test_cf_primary_falls_back_to_ollama(make_client, fake, cf_route, caplog, how):
    if how == "unreachable":
        cf_route.side_effect = httpx.ConnectError("down")
    elif how == "timeout":
        cf_route.side_effect = httpx.TimeoutException("slow")
    elif how == "http5":
        cf_route.return_value = httpx.Response(502, json={"errors": [{"code": 5000}]})
    elif how == "http408":
        cf_route.return_value = httpx.Response(408, json={"errors": [{"code": 3046}]})
    elif how == "http429":
        cf_route.return_value = httpx.Response(429, headers={"Retry-After": "3"})
    elif how == "http401":
        cf_route.return_value = httpx.Response(401, json={"errors": [{"code": 1000}]})
    elif how == "http403":
        cf_route.return_value = httpx.Response(403, json={"errors": [{"code": 1000}]})
    elif how == "paid":
        cf_route.return_value = _paid()
    else:
        cf_route.return_value = _ok("???")
    client = make_client(**_primary(fake))
    with caplog.at_level(logging.WARNING):
        response = post_image(client, data=PHOTO)
    assert response.status_code == 200
    body = response.json()
    assert body["backend_used"] == "ollama"
    assert body["model_used"]
    assert "Workers Paid" not in response.text
    assert len(fake.chats()) == 1
    if how == "paid":
        assert client.app.state.backend.primary.plan_rejections == 1
        assert any("requires Workers Paid" in rec.getMessage() for rec in caplog.records)
    if how == "http408":
        assert client.app.state.backend.primary.transient_failures == 1
    _secrets_absent(response.text)


def test_empty_after_repair_falls_back(make_client, fake, cf_route):
    empty = httpx.Response(200, json={"success": True, "result": {"response": ""}})
    cf_route.side_effect = [empty, empty]
    client = make_client(**_primary(fake))
    body = post_image(client, data=PHOTO).json()
    assert body["backend_used"] == "ollama"
    assert cf_route.call_count == 2


def test_budget_used_up_does_not_call_cloudflare(make_client, fake, cf_route):
    client = make_client(**_primary(fake, CF_DAILY_NEURON_BUDGET="0"))
    body = post_image(client, data=PHOTO).json()
    assert body["backend_used"] == "ollama"
    assert cf_route.call_count == 0


def test_oversize_photo_stays_on_ollama(make_client, fake, cf_route, monkeypatch):
    monkeypatch.setattr("app.backends.cloudflare.encode_for_cloudflare", lambda *_a, **_k: None)
    client = make_client(**_primary(fake))
    body = post_image(client, data=PHOTO).json()
    assert body["backend_used"] == "ollama"
    assert cf_route.call_count == 0


def test_cf_client_4xx_does_not_fall_back(make_client, fake, cf_route):
    client = make_client(**_primary(fake, MAX_IMAGE_BYTES="100"))
    assert client.post("/v1/suggest", data={}).status_code == 400
    assert post_image(client, data=PHOTO).status_code == 413
    assert post_image(client, data=GIF, filename="x.gif").status_code == 415
    assert cf_route.call_count == 0
    assert fake.chats() == []


def test_cf_400_does_not_fall_back(make_client, fake, cf_route):
    cf_route.return_value = httpx.Response(400, json={"errors": [{"code": 1001}]})
    client = make_client(**_primary(fake))
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_error"
    assert fake.chats() == []


def test_both_down_uses_the_503_shape(make_client, fake, cf_route):
    fake.chat_status = 500
    cf_route.return_value = httpx.Response(502, json={"errors": [{"code": 1}]})
    client = make_client(**_primary(fake))
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "backend_unavailable"
    assert body["error"]["message"] == BOTH_FAILED


def test_cold_timeout_finishes_within_the_deadline(make_client, fake, cf_route, monkeypatch):
    fake.loaded = []
    clock = _Clock()
    monkeypatch.setattr("app.backends.failover.time.monotonic", clock)
    monkeypatch.setattr("app.backends.cloudflare.time.monotonic", clock)

    def fail(_request):
        clock.t += 8
        return httpx.Response(500, json={"errors": [{"code": 5000}]})

    cf_route.side_effect = fail
    client = make_client(**_primary(fake))
    wall = time.time()
    response = post_image(client, data=PHOTO)
    assert time.time() - wall < 5
    assert clock.t - 1_000 < 45
    assert response.status_code == 200
    assert response.json()["backend_used"] == "ollama"
    assert cf_route.call_count == 1


def test_insufficient_time_is_503_with_retry_after(make_client, fake, cf_route, monkeypatch):
    fake.loaded = []
    clock = _Clock()
    monkeypatch.setattr("app.backends.failover.time.monotonic", clock)
    monkeypatch.setattr("app.backends.cloudflare.time.monotonic", clock)

    def fail(_request):
        clock.t += 40
        return httpx.Response(500, json={"errors": [{"code": 5000}]})

    cf_route.side_effect = fail
    client = make_client(**_primary(fake))
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert response.json()["error"]["message"] == NOT_ENOUGH_TIME
    assert int(response.headers["retry-after"]) == 35
    assert fake.chats() == []
    assert cf_route.call_count == 1
    assert NOT_ENOUGH_TIME != BOTH_FAILED


def test_ollama_fallback_timeout_uses_the_remaining_deadline(
    make_client, fake, cf_route, monkeypatch
):
    fake.loaded = []
    fake.chat_delay = 30
    clock = _Clock()
    monkeypatch.setattr("app.backends.failover.time.monotonic", clock)

    def fail(_request):
        clock.t += 20
        return httpx.Response(500, json={"errors": [{"code": 5000}]})

    cf_route.side_effect = fail
    client = make_client(**_primary(fake))
    wall = time.time()
    response = post_image(client, data=PHOTO)
    # Remaining after the jump is 25 s, under the 35 s cold reserve, so Ollama
    # is not started. A warm reserve would start it; this case is the refusal.
    assert response.status_code == 503
    assert response.json()["error"]["message"] == NOT_ENOUGH_TIME
    assert time.time() - wall < 5
    assert fake.chats() == []


def test_ollama_timeout_inside_the_reserve_is_503(make_client, fake, cf_route, monkeypatch):
    """Remaining time covers the warm reserve, and the fallback wait is that remaining time."""
    clock = _Clock()
    monkeypatch.setattr("app.backends.failover.time.monotonic", clock)
    monkeypatch.setattr("app.backends.cloudflare.time.monotonic", clock)
    ollama = {"started": False}
    real_wait = asyncio.wait_for

    async def wait_for(awaitable, timeout):
        # Health probes are 1–2 s, the warm Cloudflare slice is 20 s, and the
        # outer client budget is 50 s. The fallback wait is the time left on
        # the 45 s deadline, about 25 s after the Cloudflare failure.
        if timeout is not None and 20 < timeout < 40:
            ollama["started"] = True
            ollama["timeout"] = timeout
            awaitable.close()
            raise TimeoutError
        return await real_wait(awaitable, timeout)

    monkeypatch.setattr("app.backends.failover.asyncio.wait_for", wait_for)

    def fail(_request):
        clock.t += 20
        return httpx.Response(500, json={"errors": [{"code": 5000}]})

    cf_route.side_effect = fail
    client = make_client(**_primary(fake))
    ollama_backend = client.app.state.backend.fallback
    ollama_backend._succeeded.add(model_key(ollama_backend.model))
    fake.size_vram = 4096
    response = post_image(client, data=PHOTO)
    assert ollama["started"] is True
    assert 24 <= ollama["timeout"] <= 26
    assert response.status_code == 503
    assert response.json()["error"]["message"] == LOCAL_DEADLINE
    assert response.headers["retry-after"] == "15"


def test_healthz_reports_local_ollama_while_cloudflare_serves(make_client, fake, cf_route):
    fake.size_vram = 0
    client = make_client(**_primary(fake))
    probe = client.get("/healthz")
    assert probe.status_code == 503
    assert probe.json() == {"status": "degraded", "serving": "primary"}
    assert_contract(probe, "/healthz")
    only = make_client(BACKEND="cloudflare", FALLBACK_BACKEND="none", **_cf_env())
    assert only.get("/healthz").json() == {"status": "ok"}


def test_warmup_still_runs_when_cloudflare_is_primary(make_client, fake):
    fake.warm_status = 500
    client = make_client(**_primary(fake, OLLAMA_WARMUP="1"))
    with client:
        thread = client.app.state.backend.fallback._warm_thread
        assert thread is not None and thread.is_alive()


def test_default_budget_constant_is_6000(monkeypatch):
    monkeypatch.delenv("CF_DAILY_NEURON_BUDGET", raising=False)
    assert get_settings().cf_daily_neuron_budget == 6000


@pytest.mark.parametrize("how", ["paid", "401", "403"])
def test_can_serve_is_false_after_rejection_and_recovers(oclient, fake, cf_route, how):
    fake.size_vram = 0
    if how == "paid":
        cf_route.side_effect = [_paid(), _ok()]
    elif how == "401":
        cf_route.side_effect = [httpx.Response(401, json={"errors": [{"code": 1000}]}), _ok()]
    else:
        cf_route.side_effect = [httpx.Response(403, json={"errors": [{"code": 1000}]}), _ok()]
    # The paid fixture reports 7777 neurons, which would exhaust the default
    # 6000 budget and hide the auth-block recovery this test is about.
    client = oclient(
        FALLBACK_BACKEND="cloudflare",
        CF_DAILY_NEURON_BUDGET="20000",
        **_cf_env(),
    )
    assert post_image(client, data=PHOTO).status_code == 503
    info = client.get("/v1/info").json()
    assert info["backends"]["fallback"]["can_serve"] is False
    assert info["backends"]["fallback"]["state"] == "unavailable"
    assert client.get("/healthz").json() == {"status": "degraded"}
    recovered = post_image(client, data=PHOTO)
    assert recovered.status_code == 200
    assert recovered.json()["backend_used"] == "cloudflare"
    assert client.get("/v1/info").json()["backends"]["fallback"]["can_serve"] is True
    assert client.get("/healthz").json() == {"status": "degraded", "serving": "fallback"}


def test_used_up_budget_clears_can_serve_until_the_utc_day_resets(
    oclient, fake, monkeypatch, tmp_path
):
    fake.size_vram = 0
    path = tmp_path / "budget.json"
    today = datetime.now(UTC).date().isoformat()
    path.write_text(json.dumps({"day": today, "used": 6000}), encoding="utf-8")
    client = oclient(
        FALLBACK_BACKEND="cloudflare",
        CF_BUDGET_STATE_FILE=str(path),
        **_cf_env(),
    )
    assert client.get("/v1/info").json()["backends"]["fallback"]["can_serve"] is False
    assert client.get("/healthz").json() == {"status": "degraded"}
    monkeypatch.setattr(
        "app.backends.budget.utc_now",
        lambda: datetime.now(UTC) + timedelta(days=1),
    )
    assert client.get("/v1/info").json()["backends"]["fallback"]["can_serve"] is True
    assert client.get("/healthz").json() == {"status": "degraded", "serving": "fallback"}


def test_missing_cloudflare_config_cannot_serve(oclient, fake):
    fake.size_vram = 0
    client = oclient(FALLBACK_BACKEND="cloudflare", CF_DAILY_NEURON_BUDGET="0")
    info = client.get("/v1/info").json()
    assert info["backends"]["fallback"]["can_serve"] is False
    assert client.get("/healthz").json() == {"status": "degraded"}
    assert "serving" not in client.get("/healthz").json()
