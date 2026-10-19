# Cloudflare Workers AI backend

Optional second backend for the autofill service. It is **off by default**.
Ollama on the Jetson stays the primary. Turning Cloudflare on sends the photo
and the brand, model number, and serial number off the premises. Do that only
as an explicit opt-in.

The service calls the Workers AI REST API itself:

`POST https://api.cloudflare.com/client/v4/accounts/<account-id>/ai/run/<model>`

with `Authorization: Bearer <api-token>`. There is no Worker in this repo. An
optional proxy is described below for operators who want the token off the
board; the service does not call it unless you point something else at it.

API contract changes are in [api-v1.md](api-v1.md). `version` stays `"1"`.

## Account and API token

1. Create a Cloudflare account. Workers AI is available on the free plan, with
   a daily Neuron allowance. Some models require Workers Paid; the provisional
   default does not.
2. Copy the account id into `CF_ACCOUNT_ID`. It is not a secret in the same way
   as the token, but this service still never logs it or puts it in an error.
3. Create an API token. Use the **Workers AI** template, or a custom token with
   account permission **Workers AI — Read**. That is enough to run a model.
   Do not grant unrelated account permissions.
4. Put the token only in `CF_API_TOKEN`. See [DevOps (to be finished)](#devops-to-be-finished)
   for the board. Never pass it on a command line, in a container label, or in
   a shell history you keep.

Placeholders, not real values:

```bash
CF_ACCOUNT_ID=<account-id>
CF_API_TOKEN=<workers-ai-token>
```

Startup refuses to boot if Cloudflare is the primary or the fallback and either
value is missing. The error names the variables and does not print the values.

## Models

`CF_MODEL` is the Workers AI model id. It is fully configurable. The
provisional default is `@cf/google/gemma-4-26b-a4b-it` (Gemma 4 26B A4B). It
accepts images, supports `response_format` with a JSON schema, is Apache 2.0,
and stays on the free plan. **Provisional:** ITAdvisor's accuracy comparison
against the Jetson model decides whether it stays the default.

Candidates until that comparison:

| Model | Id | Structured output | Note |
|---|---|---|---|
| Gemma 4 26B A4B | `@cf/google/gemma-4-26b-a4b-it` | `response_format` JSON schema | Provisional default. Vision. Free plan. |
| Qwen 3.8 27B | `@cf/qwen/qwen3.8-27b` | `response_format` JSON schema | Vision. Candidate. |
| Mistral Small 3.1 24B | `@cf/mistralai/mistral-small-3.1-24b-instruct` | `guided_json` | Only if image input is confirmed on its model page. Not the default. |

Other Qwen 3 ids (`@cf/qwen/qwen3…`) use `response_format` as well. A model
that is not in that table gets the schema written into the prompt. The service
always validates the answer itself. If that prompt-schema answer is unusable
it retries **once**. It does not retry a Workers Paid rejection.

Check each model page before enabling it. The paid-only list is not stable:
<https://developers.cloudflare.com/changelog/post/2026-07-28-models-require-workers-paid/>.

## Rejected models

Llama 3.2 Vision (`@cf/meta/llama-3.2-11b-vision-instruct`) and Llama 4 Scout
(`@cf/meta/llama-4-scout-17b-16e-instruct`) are **rejected**. The license gives
no rights to EU-based users, and that includes test runs. They are not an
opt-in. They are not in the structured-output table. If `CF_MODEL` is one of
them, the process refuses to start.

Meta's Llama 3.2 Acceptable Use Policy says:

> With respect to any multimodal models included in Llama 3.2, the rights granted under Section 1(a) of the Llama 3.2 Community License Agreement are not being granted to you if you are an individual domiciled in, or a company with a principal place of business in, the European Union.

Llama 4's acceptable use policy has the same restriction for its multimodal
models.

## Environment

### Primary mode

Cloudflare answers every suggestion. `/healthz` does not report a GPU
residency for it; there is no local model to degrade.

```bash
BACKEND=cloudflare
CF_ACCOUNT_ID=<account-id>
CF_API_TOKEN=<workers-ai-token>
CF_MODEL=@cf/google/gemma-4-26b-a4b-it
CF_TIMEOUT_S=20
```

### Backup mode

Ollama stays primary. Cloudflare is used only for the failures listed below.
This is the mode for the Jetson.

```bash
BACKEND=ollama
FALLBACK_BACKEND=cloudflare
FAILOVER_ON_COLD=false
CF_ACCOUNT_ID=<account-id>
CF_API_TOKEN=<workers-ai-token>
CF_MODEL=@cf/google/gemma-4-26b-a4b-it
CF_TIMEOUT_S=20
```

`FALLBACK_BACKEND=cloudflare` is valid only with `BACKEND=ollama`. The default
`FALLBACK_BACKEND` is `none`. With that default the service does not call
Cloudflare at all.

### Failover rules

From Ollama to Cloudflare, only when `FALLBACK_BACKEND=cloudflare`:

- Ollama is unreachable
- Health is degraded (a warm model, or the loaded production model, has
  `size_vram` 0)
- The Ollama call times out
- Ollama returns an upstream 5xx
- The local backend is busy or its queue is full (503)

A cold start (warming up, or loading a model, which would be 503 with
`Retry-After`) fails over **only** when `FAILOVER_ON_COLD=true`. The default
is `false`. When it is true, the Ollama warm-up or background load keeps
running, and the next request uses Ollama again once that model is warm.

The service does **not** fail over on a client 4xx (400, 413, 415, and any
other 4xx, including an upstream Ollama 4xx).

If the photo cannot be re-encoded under the Cloudflare size cap, it stays
local: no Cloudflare call.

If both sides fail, the client gets **503** with the existing error object
`{"error":{"code":"...","message":"..."}}`. A Cloudflare 403 whose errors
contain code **5035** (the model requires Workers Paid) is not retried and is
not counted as a transient 5xx. As a fallback, the client still sees that
normal both-sides 503. The log has one line naming the model and saying it
needs Workers Paid. The token and the account id are not in that line.

The Ollama wait plus the Cloudflare call stays under Apotheke's client
timeout, `BACKEND_TIMEOUT_S` + 5 seconds (50 s when the backend timeout is
45 s). The split is taken from `BACKEND_TIMEOUT_S` and `CF_TIMEOUT_S`.

### What the client sees

- `backend_used`: `ollama` or `cloudflare`
- `model_used`: the Ollama tag, or the real `CF_MODEL` id when Cloudflare served
- `GET /v1/info` field `backends.primary` and `backends.fallback`, including
  `fallback.can_serve`
- `GET /healthz` still reports only the local primary. Degraded stays HTTP 503.
  If the primary is degraded and the fallback can serve, the body is
  `{"status":"degraded","serving":"fallback"}`.

Photos are re-encoded to JPEG, long edge 560 px, and dropped instead of sent
if they cannot fit in about 1.5 MB. Image bytes are not logged. Brand, model
number, and serial number go through the same trim, 128-character cap, and
control / surrogate / bidi stripping as the Ollama path, and they are not
logged. If a response includes usage or neuron counts, only those numbers are
logged, at DEBUG, never the prompt or the answer.

## Optional wrangler proxy

Not used by the service. Use it only if you want the API token to live in a
Worker secret instead of on the board. The board would then call your Worker,
and the Worker would call Workers AI with an `AI` binding so it does not need
the token at all. This repository does not ship that Worker.

Sketch only, with placeholders:

```bash
# wrangler.toml (not in this repo)
# name = "autofill-ai-proxy"
# account_id = "<account-id>"
#
# [ai]
# binding = "AI"
```

```javascript
export default {
  async fetch(request, env) {
    if (request.headers.get("Authorization") !== `Bearer ${env.PROXY_TOKEN}`) {
      return new Response("unauthorized", { status: 401 });
    }
    const body = await request.json();
    const result = await env.AI.run("<model-id>", body);
    return Response.json(result);
  },
};
```

Set the Worker secret `PROXY_TOKEN` with `wrangler secret put PROXY_TOKEN`.
Do not put the Workers AI token in the Worker source. The autofill service, as
shipped, does not read a proxy URL. Pointing the service at a proxy would be a
later change.

## Verify

No real token belongs in a shell history. Export the variables from a file
only for a local check, then unset them.

Primary:

```bash
BACKEND=cloudflare \
CF_ACCOUNT_ID=<account-id> \
CF_API_TOKEN=<workers-ai-token> \
CF_MODEL=@cf/google/gemma-4-26b-a4b-it \
uvicorn app.main:app --port 8080
```

```bash
curl -sS localhost:8080/healthz
curl -sS localhost:8080/v1/info
curl -sS -F image=@photo.jpg localhost:8080/v1/suggest
```

Expect `backend` `cloudflare` on `/v1/info`, and `backend_used` `cloudflare`
plus `model_used` equal to `CF_MODEL` on a successful suggest. `/healthz` is
`{"status":"ok"}` (there is no local GPU probe in this mode).

Backup, Ollama healthy: the same curl against `BACKEND=ollama` and
`FALLBACK_BACKEND=cloudflare` returns `backend_used` `ollama`. Stop Ollama, or
point `OLLAMA_URL` at a closed port, and the same suggest returns
`backend_used` `cloudflare`. Start Ollama and warm the model; the next suggest
returns to `ollama`.

`FAILOVER_ON_COLD=false` (the default): while the model is warming,
`/v1/suggest` is 503 with `Retry-After` and Cloudflare is not called.

Confirm the token is absent from the log:

```bash
# container or local process log, after a suggest
grep -F '<workers-ai-token>'  # must print nothing
```

## Cost

Stay on the **Workers free plan**. Workers Paid is $5 per month and is not
required for the provisional default. The free plan includes 10,000 Neurons
per day. Above that, paid usage is billed per 1,000 Neurons. Some models
require Workers Paid; a free-plan call then returns HTTP 403 with error code
5035. The list is here:

<https://developers.cloudflare.com/changelog/post/2026-07-28-models-require-workers-paid/>

Check the model page before you set `CF_MODEL`. Gemma 4 26B A4B is on the free
plan as of the 2026-07-28 note. That can change.

Research estimated about 29 Neurons per photo for Llama 3.2 11B Vision. That
figure is only an estimate from that research, for a model this service
rejects. It is not a measured cost for Gemma 4 or Qwen 3.8. Per-model neuron
counts will be taken from each response's usage (logged as numbers only, at
DEBUG). Read the Cloudflare dashboard after a pilot before treating any number
as a budget.

## License

The provisional default, Gemma 4 26B A4B, is Apache 2.0. Qwen 3.8 27B is
Apache 2.0 as published by Alibaba. Confirm the model card before you rely on
that. Mistral Small 3.1 is Apache 2.0, and it is not selected unless image
input is confirmed.

Rejected models are not licensed to us for EU use. Do not enable them.

## Privacy

A Cloudflare suggestion sends the photo and the fields the model returns,
including brand, model number, and serial number, to Cloudflare. Inference is
not kept in Greece. Workers AI sub-processors include CoreWeave in the US and
Nebius in the UK. Cloudflare's own docs describe standard inference as not
used for training; logs, AI Gateway payload logs, and anything we store would
be a copy. This path does not enable those.

The path is opt-in and off by default (`FALLBACK_BACKEND=none`, and the
Jetson `BACKEND` stays `ollama`). Logging of prompts, answers, and image bytes
is off on our side. Usage counts, when the response includes them, are numbers
only, at DEBUG. With `FAILOVER_ON_COLD` left false, only a real failure (down,
degraded, timeout, upstream 5xx, busy) is sent to Cloudflare. A cold model
stays a local 503.

## DevOps (to be finished)

This section is for DevOps. The application code does not read a token from
anywhere except the environment.

- Put `CF_API_TOKEN` and `CF_ACCOUNT_ID` in `/opt/autofill/service.env` on the
  board, the same file as `AUTOFILL_TOKEN`.
- Owner root, mode `600`. The container must keep receiving it with
  `docker run --env-file`. Never as `-e` on the command line, never in a
  compose label, never in `ps`.
- Do not commit the file. `deploy/jetson/service.env.example` stays a template
  without a real token.
- After the first suggest with the fallback enabled, confirm the token string
  is not in the container logs (`docker logs` / the journal). A DEBUG log may
  show neuron counts. It must not show the token, the account id, the image,
  or brand / model / serial values.
- Leave `FALLBACK_BACKEND=none` until that check is done. Primary stays
  `BACKEND=ollama`.
