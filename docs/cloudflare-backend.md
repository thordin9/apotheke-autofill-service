# Cloudflare Workers AI backend

Shipped default for the autofill service: `BACKEND=cloudflare` and
`FALLBACK_BACKEND=ollama`. When `CF_API_TOKEN` and `CF_ACCOUNT_ID` are set, every
photo, and the brand, model number, and serial number the model reads, goes to
Cloudflare. When either value is missing, or the budget state file is missing or
unreadable, the process does not crash: it logs one warning and serves from Ollama.
`BACKEND=ollama` with `FALLBACK_BACKEND=none` stays local and makes no Cloudflare call.

The service calls the Workers AI REST API itself:

`POST https://api.cloudflare.com/client/v4/accounts/<account-id>/ai/run/<model>`

with `Authorization: Bearer <api-token>`. There is no Worker in this repo. An
optional proxy is described below for operators who want the token off the
board; the service does not call it unless you point something else at it.

API contract changes are in [api-v1.md](api-v1.md). `version` stays `"1"`.

## Account and API token

1. Create a Cloudflare account. Workers AI is available on the free plan, with
   a daily Neuron allowance (10,000 Neurons per account, shared by every
   caller on that account, including benches and QA). Some models require
   Workers Paid; the default below does not.
2. Copy the account id into `CF_ACCOUNT_ID`. It is not a secret in the same way
   as the token, but this service still never logs it or puts it in an error.
3. Create an API token. Use the **Workers AI** template, or a custom token with
   account permission **Workers AI — Read**. That is enough to run a model.
   Do not grant unrelated account permissions.
4. Put the token only in `CF_API_TOKEN`. See [DevOps](#devops) for the board.
   Never pass it on a command line, in a container label, or in a shell history
   you keep.

Placeholders, not real values:

```bash
CF_ACCOUNT_ID=<account-id>
CF_API_TOKEN=<workers-ai-token>
```

Startup refuses to boot if Cloudflare is the primary or the fallback, the
daily budget is above 0, and either value is missing. The error names the
variables and does not print the values. `CF_DAILY_NEURON_BUDGET=0` disables
Cloudflare without credentials, because the service never calls it.

## Models

`CF_MODEL` is the Workers AI model id. The confirmed default, from the
19-photo bench in
[bench/results/cf-workers-ai-2026-10-19](../bench/results/cf-workers-ai-2026-10-19/),
is `@cf/google/gemma-4-26b-a4b-it` (Gemma 4 26B A4B). It accepts images,
answers with `response_format` JSON schema, is Apache 2.0, and stays on the
free plan. The service always sends `chat_template_kwargs.enable_thinking`
false for this model. That flag is not on the model page; the bench verified
the endpoint accepts it. Thinking on used about 3.4× the Neurons and 4× the
p50, and one photo (`panasonic_vcr_label`) spent the whole 1024-token cap on
reasoning and returned an empty answer.

| Model | Id | Structured output | Neurons / 1M tokens (in, out) | Note |
|---|---|---|---|---|
| Gemma 4 26B A4B | `@cf/google/gemma-4-26b-a4b-it` | `response_format` JSON schema | 9091, 27273 | Confirmed default. Vision. Free plan. Thinking off. |
| Mistral Small 3.1 24B | `@cf/mistralai/mistral-small-3.1-24b-instruct` | `response_format` JSON schema | 31876, 50488 | Image input was confirmed on the bench. Not the default (7 failures vs Gemma's 3). `guided_json` is ignored once the message contains an image. |

Rates are from the Workers AI pricing page on 2026-10-19
(<https://developers.cloudflare.com/workers-ai/platform/pricing/>). A reply's
`usage.neurons` is what the budget charges when Cloudflare sends it. The rate
table is used only when that field is missing. Qwen ids under `/qwen/qwen3`
still get `response_format` if someone sets `CF_MODEL` to one.

### Not recommended

Qwen 3.8 27B (`@cf/qwen/qwen3.8-27b`) is not a candidate. On the same bench one
photo returned HTTP 408 after 120 s (Workers AI error 3046), it cost 7–16×
the Neurons (44.9 with thinking off, 103.9 with reasoning low), and it did
not beat Gemma's accuracy. Rates if it is configured anyway: input 40909,
output 290909, cached input 4545 per 1M tokens. HTTP 408 is treated as a
timeout and counted as a transient failure. An unknown model is charged at
the Qwen rates so it cannot slip through cheap.

Other models get the schema written into the prompt and one repair retry.
The service always validates the answer itself. It also strips a ```json
fence before validation. An empty reply, `finish_reason` `length`, or a
completion that used the whole `max_tokens` (1024) takes that same one repair,
including Gemma and Mistral. A second failure is an unusable response. The
repair is skipped when less than a second remains in the Cloudflare slice.
Workers Paid rejections are not retried inside the request.

Check each model page before enabling it. The paid-only list is not stable:
<https://developers.cloudflare.com/changelog/post/2026-07-28-models-require-workers-paid/>.

## Rejected models

Llama 3.2 Vision (`@cf/meta/llama-3.2-11b-vision-instruct`) and Llama 4 Scout
(`@cf/meta/llama-4-scout-17b-16e-instruct`) are **rejected**. The license gives
no rights to EU-based users, and that includes test runs. They are not an
opt-in. They are not in the structured-output table. The check is
case-insensitive. If `CF_MODEL` is one of them, the process refuses to start.

Meta's Llama 3.2 Acceptable Use Policy says:

> With respect to any multimodal models included in Llama 3.2, the rights granted under Section 1(a) of the Llama 3.2 Community License Agreement are not being granted to you if you are an individual domiciled in, or a company with a principal place of business in, the European Union.

Llama 4's acceptable use policy has the same restriction for its multimodal
models.

## Environment

### Shipped default: Cloudflare primary, Ollama fallback

This is what the service starts with. Cloudflare answers when a token is set
and the budget can pay. Ollama stays loaded (`OLLAMA_KEEP_ALIVE=-1` on the
board) and takes the request when Cloudflare cannot, including when the token
or account id is missing or the budget state file is missing or unreadable.
`deploy/jetson/service.env.example` ships these two backend values. Replace
the placeholders with the board's own secret file; do not commit that file.

```bash
BACKEND=cloudflare
FALLBACK_BACKEND=ollama
BACKEND_TIMEOUT_S=45
REQUEST_DEADLINE_S=45
CF_TIMEOUT_S=20
OLLAMA_WARM_RESERVE_S=15
OLLAMA_COLD_RESERVE_S=35
CF_DAILY_NEURON_BUDGET=6000
CF_BUDGET_STATE_FILE=/var/lib/autofill/cf-neuron-budget.json
CF_ACCOUNT_ID=<account-id>
CF_API_TOKEN=<workers-ai-token>
CF_MODEL=@cf/google/gemma-4-26b-a4b-it
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5vl-jetson:3b
OLLAMA_KEEP_ALIVE=-1
OLLAMA_WARMUP=1
```

`FALLBACK_BACKEND=ollama` is valid only with `BACKEND=cloudflare`.

One deadline covers the request. `REQUEST_DEADLINE_S` defaults to 45 and must
be under Apotheke's 50 s client timeout, and it must fit inside
`BACKEND_TIMEOUT_S`. Startup refuses to boot when it does not, and the message
does not print secrets.

The Ollama reserve depends on the local model, from the same cold / loading /
warm tracker `/v1/info` already uses:

- warm: `OLLAMA_WARM_RESERVE_S` (default 15). The Jetson warm p95 at long edge
  560 is about 11 s; 15 s leaves headroom.
- cold or loading: `OLLAMA_COLD_RESERVE_S` (default 35). A cold Qwen load on
  the board measured 25–32 s.

The effective Cloudflare slice is

`min(CF_TIMEOUT_S, REQUEST_DEADLINE_S - reserve - repair allowance)`

and at least 5 s. A second full `CF_TIMEOUT_S` is reserved as the repair
allowance only when it fits beside the first call. With the defaults it does
not, so the allowance is 0 and a repair runs only when the first call returns
with time left inside the slice.

Worked example, recommended defaults:

- warm: `min(20, 45 - 15 - 0) = 20`. Then 20 s Cloudflare + 15 s reserve = 35 s,
  under the 45 s deadline and under the 50 s client timeout.
- cold: `min(20, 45 - 35 - 0) = 10`. Then 10 + 35 = 45, still under 50 s.

`CF_TIMEOUT_S`, `REQUEST_DEADLINE_S`, and both reserves must be finite numbers
greater than 0. `nan` and `inf` are refused at startup.

If Cloudflare fails and the time left is less than the reserve the local model
needs now, the service does not start Ollama. The client gets 503
`not enough time left to fall back to the local model` with `Retry-After`.
When Ollama is started, its wait is the time still left on the deadline.

Failover from Cloudflare to Ollama happens on: unreachable, timeout (including
HTTP 408), upstream 5xx, 429, 401/403 (including Workers Paid error 5035), a
malformed or empty body after the one repair, a used-up daily Neuron budget,
or a photo that will not fit the Cloudflare size cap after re-encoding.
Client 4xx on the incoming request (400, 413, 415) is returned directly and
does not call either backend. A Cloudflare HTTP 400 does not fall back either.

Ollama cold or loading, when it is the fallback, waits when the remaining
deadline covers it and otherwise returns 503 with `Retry-After`. Busy from
Ollama is passed through with its own `Retry-After`.

`/v1/info` shows the effective slice, the reserve, and the Ollama state on the
Cloudflare primary entry (`request_deadline_s`, `cf_slice_s`,
`ollama_reserve_s`, `ollama_state`). No secrets.

### Alternative: Ollama primary, Cloudflare fallback

Ollama stays primary. Cloudflare is used only for the failures listed below.

```bash
BACKEND=ollama
FALLBACK_BACKEND=cloudflare
FAILOVER_ON_COLD=false
CF_ACCOUNT_ID=<account-id>
CF_API_TOKEN=<workers-ai-token>
CF_MODEL=@cf/google/gemma-4-26b-a4b-it
CF_TIMEOUT_S=20
CF_DAILY_NEURON_BUDGET=6000
```

`FALLBACK_BACKEND=cloudflare` is valid only with `BACKEND=ollama`. The shipped
default `FALLBACK_BACKEND` is `ollama`, which requires `BACKEND=cloudflare`.
`FALLBACK_BACKEND=none` with `BACKEND=ollama` does not call
Cloudflare at all.

From Ollama to Cloudflare, only when `FALLBACK_BACKEND=cloudflare`:

- Ollama is unreachable
- Health is degraded (a warm model, or the loaded production model, has
  `size_vram` 0)
- The Ollama call times out
- Ollama returns an upstream 5xx
- The local backend is busy or its queue is full (503)

A cold start fails over **only** when `FAILOVER_ON_COLD=true`. The default is
`false`. Warm-up keeps running, and the next request uses Ollama again once
that model is warm.

The service does **not** fail over on a client 4xx, including an upstream
Ollama 4xx. A photo that cannot be re-encoded under the Cloudflare size cap
stays local: no Cloudflare call. If the Neuron budget cannot pay, the request
stays local exactly as if `FALLBACK_BACKEND` were `none`.

If Cloudflare was called and both sides fail, the client gets **503**
`{"error":{"code":"backend_unavailable","message":"the primary backend and the fallback backend failed"}}`.
A Cloudflare 403 whose errors contain code **5035** (Workers Paid) is not
retried inside the request and is not counted as a transient 5xx. As a
fallback, the client still sees that both-sides 503. The log has one warning
naming the model. The token and the account id are not in that line.

On this path the Ollama wait plus the Cloudflare call stays under
`BACKEND_TIMEOUT_S` + 5 seconds.

### What the client sees

- `backend_used`: `ollama` or `cloudflare`, whoever actually answered
- `model_used`: that backend's model id
- `GET /v1/info` `backends.primary` and `backends.fallback`. The Cloudflare
  entry includes `model` and `budget` (`limit`, `used`, `remaining`,
  `resets_at`, and `reason` when Cloudflare will not be called). `can_serve`
  is false when the budget cannot pay, credentials are missing, the budget is
  0, or the last Cloudflare call was 401, 403, or Workers Paid (5035). A later
  successful call clears an auth or plan failure. A new UTC day clears a
  used-up budget.
- `GET /healthz` reports the **local Ollama** even when Cloudflare is primary,
  so a deploy rollback still sees `size_vram` 0. Degraded stays HTTP 503. The
  body adds `"serving":"fallback"` when Ollama is primary and Cloudflare can
  serve, or `"serving":"primary"` when Cloudflare is primary and it can serve.
  The field is omitted when Cloudflare cannot serve. With no local backend,
  `/healthz` stays `{"status":"ok"}`.

Photos are re-encoded off the event loop: EXIF orientation is applied, then
JPEG, long edge 560 px, and dropped instead of sent if they cannot fit in
about 1.5 MB. Image bytes are not logged. Brand, model number, and serial
number are not logged. Usage counts (tokens and neurons) and the model id are
logged at INFO. The API token is not.

## Daily Neuron budget

`CF_DAILY_NEURON_BUDGET` defaults to **6000**. `0` disables Cloudflare. The
counter resets at 00:00 UTC.

6000 at the measured 6.4 Neurons per Gemma 4 photo is about **930** photos a
day. The pre-call check is stricter than that average: before each call,
including a repair, the service reserves a conservative cost (1500 prompt
tokens and 1024 completion tokens; about 42 Neurons for Gemma) and does not
call Cloudflare when less than that remains. Replies that include
`usage.neurons` are charged that number. Replies with tokens but no `neurons`
field use the rate table. Replies with no usage at all (timeout, HTTP 408,
5xx without a body) are charged the conservative estimate, and a DEBUG line
says the charge was estimated. Failed and empty replies that returned usage
count too.

The counter only sees **this service's** calls. Cloudflare's 10,000-Neuron
free-plan limit is per account. Benches and QA share it. Set the budget below
10,000 and leave headroom. The default 6000 does that.

State file: `CF_BUDGET_STATE_FILE`, default
`/var/lib/autofill/cf-neuron-budget.json`. That path is the container side of
a host directory, not `/tmp`, so recreating the container keeps the counter.
On the Jetson, `run-service.sh` mounts `-v /opt/autofill/data:/var/lib/autofill`.
Create the directory once:

```bash
install -d -m 755 /opt/autofill/data
```

The file is JSON `{"day":"YYYY-MM-DD","used":n}` and nothing else. Writes are
atomic (a temporary file, then rename).

Fail-safe: a corrupt, unreadable, or unwritable state file, including a failed
rename after a Cloudflare call, spends the budget for the rest of the UTC day.
No further Cloudflare calls. Requests stay on Ollama, or 503
`daily Cloudflare budget used up, resets 00:00 UTC` with `Retry-After` when
there is no local backend. A WARNING names the problem and does not include
secrets. `/v1/info` shows `remaining` 0 and a `reason`. The corrupt file is
not overwritten with zeros. The only start-at-0 case is a missing file on
first start when creating it succeeds. A missing file that cannot be created
is used up. A new UTC day resets a readable, valid file.

When Cloudflare is primary and the budget blocks the call, Ollama answers if
it is the fallback (its own errors, not the both-sides message, because
Cloudflare was not called). Two overlapping calls cannot both pass the check:
the expected cost is reserved under a lock before the HTTP call.

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

Primary, with the deadline that fits a 45 s backend timeout:

```bash
BACKEND=cloudflare \
FALLBACK_BACKEND=none \
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

Expect `backend` `cloudflare` on `/v1/info`, a `budget` object under
`backends.primary`, and `backend_used` `cloudflare` plus `model_used` equal
to `CF_MODEL` on a successful suggest. `/healthz` is `{"status":"ok"}` when
there is no local Ollama.

With `FALLBACK_BACKEND=ollama`, `/healthz` follows the local model. A warm
model at `size_vram` 0 is HTTP 503 `{"status":"degraded","serving":"primary"}`
while Cloudflare can serve, and `{"status":"degraded"}` with no `serving`
after a 401, a 403, Workers Paid, a used-up budget, or missing credentials.

Confirm the token is absent from the log:

```bash
# container or local process log, after a suggest
grep -F '<workers-ai-token>'  # must print nothing
```

An INFO line may show `cloudflare usage model=...` and numeric counts. It must
not show the token, the account id, the image, or brand / model / serial values.

## Cost

Stay on the **Workers free plan**. Workers Paid is $5 per month and is not
required for the default model. The free plan includes 10,000 Neurons per day
**per account**. Above that, paid usage is billed per 1,000 Neurons. Some
models require Workers Paid; a free-plan call then returns HTTP 403 with error
code 5035. The list is here:

<https://developers.cloudflare.com/changelog/post/2026-07-28-models-require-workers-paid/>

Measured on the 19-photo bench (one run per photo; p95 is rough because of
queueing), Gemma 4 with thinking off: **6.4 Neurons/photo**, p50 2.5 s, p95
4.7 s. Mistral Small 3.1: **19.4 Neurons/photo**, p50 4.2 s, p95 5.7 s. Qwen
3.8: **44.9 to 103.9** Neurons/photo. Against the Jetson
`qwen2.5vl-jetson:3b` v3_lean at 560 px: categories 19/19 vs 18/19, failures
(invented or misread) 3 vs 5, valid JSON on the first try 19/19 for both.
Gemma with thinking on was 21.8 Neurons, p50 10.4 s, p95 23.5 s, and one photo
needed a repair. Do not use that configuration.

The archive is
[bench/results/cf-workers-ai-2026-10-19](../bench/results/cf-workers-ai-2026-10-19/).

## License

The default, Gemma 4 26B A4B, is Apache 2.0. Mistral Small 3.1 is Apache 2.0.
Confirm the model card before you rely on that. Qwen 3.8 is Apache 2.0 as
published by Alibaba and is not recommended.

Rejected models are not licensed to us for EU use. Do not enable them.

## Privacy

The shipped default is `BACKEND=cloudflare` with `FALLBACK_BACKEND=ollama`.
When a token and an account id are set, **every** photo, plus brand, model
number, and serial number, is sent to Cloudflare. Inference is not kept in
Greece. Workers AI sub-processors include CoreWeave in the US and Nebius in
the UK. Cloudflare's own docs describe standard inference as not used for
training; logs, AI Gateway payload logs, and anything we store would be a
copy. This path does not enable those.

When the token or account id is missing, or the budget state file is missing
or unreadable, nothing is sent: the service logs one warning and answers from
Ollama. `BACKEND=ollama` with `FALLBACK_BACKEND=none` never calls Cloudflare.
Ollama primary with `FALLBACK_BACKEND=cloudflare` and `FAILOVER_ON_COLD` left
false sends a photo only on a real failure (down, degraded, timeout, upstream
5xx, busy). A cold model stays a local 503.

## DevOps

**Legacy.** Self-hosting this service in Docker on the Jetson is superseded by
the Cloudflare Worker (`docs/cloudflare-workers-deploy.md`, coming in the
Feature 11 PR). The Jetson is not getting v1.4. The notes below are for anyone
who still self-hosts.

**Token.** Create a custom API token with only **Account → Workers AI → Read**.
Cloudflare's Workers AI template grants Read and Edit. Read alone ran `/ai/run`
on 2026-10-19. If `/ai/run` returns an authentication error, add Workers AI
Edit. Stay on the free plan; a paid-only model returns 403 with code 5035.

**service.env.** `/opt/autofill/service.env`, root:root, mode `600`.
`run-service.sh` passes it with `docker run --env-file`, so values never show
on a command line or in `ps`. Never commit it; CI runs gitleaks over the full
history. Placeholders:

```bash
BACKEND=cloudflare
FALLBACK_BACKEND=ollama
CF_ACCOUNT_ID=<CF_ACCOUNT_ID>
CF_API_TOKEN=<CF_API_TOKEN>
CF_MODEL=@cf/google/gemma-4-26b-a4b-it
CF_DAILY_NEURON_BUDGET=6000
```

`CF_ACCOUNT_ID_FILE` / `CF_API_TOKEN_FILE` are read **inside the container**.
`run-service.sh` mounts only `/opt/autofill/data` (as `/var/lib/autofill`), so a
host path such as `/opt/autofill/cf-api-token` is not visible there; the
service then logs one warning and serves from Ollama. Use the direct values
above, or a path under `/var/lib/autofill`. Create the budget directory once:
`install -d -m 755 /opt/autofill/data`.

**Token not in logs.** After a suggest, this must print `0`. It reads the token
from the file, so the token is not on the command line:

```bash
sudo bash -c 'docker logs autofill 2>&1 | grep -cF -f <(sed -n "s/^CF_API_TOKEN=//p" /opt/autofill/service.env)'
```

**Egress.** The host must reach `api.cloudflare.com` over HTTPS. Any HTTP code
means TLS and routing work; `000` means they don't:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://api.cloudflare.com/client/v4/
```

**Budget.** The team cap is **6,000 Neurons a day, team-wide**, out of the
account's 10,000 free Neurons. Benches and QA draw from the same pool. At about
6.4 Neurons a photo (measured 2026-10-19), that is about 930 photos a day. See
[Daily Neuron budget](#daily-neuron-budget). Usage appears as an INFO line
`cloudflare usage model=...` with numbers only, and `/v1/info` shows the
`budget` under `backends`.

**Rollback.** Re-run `deploy/jetson/run-service.sh` with the previous image tag
(`docker images apotheke-autofill`), and restore the `service.env` backup if
you changed it. For local-only with v1.4, set `BACKEND=ollama` and
`FALLBACK_BACKEND=none` and remove the `CF_*` lines.
