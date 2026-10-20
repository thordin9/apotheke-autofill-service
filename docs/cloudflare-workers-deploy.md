# Deploy the autofill Worker

The Cloudflare Worker in [`worker/`](../worker) is the deployment to point
Apotheke at. Docker and the Jetson stack are legacy self-hosting; they stay
in the repo and keep the same HTTP contract, with a larger upload limit.

The Worker is Workers Free plus the Workers AI free allocation. It does not
use KV. The daily cap lives in one SQLite-backed Durable Object. Nothing here
is a paid feature. `workers_dev` and preview URLs are off. The public name is
the custom domain `apotheke-autofill-service.webapp.gr`. A fork uses its own
`<your-domain>` in `worker/wrangler.toml`.

## What you need

- Node.js 22 and npm
- A Cloudflare account on the Workers Free plan
- Wrangler 4.36 or newer. This repo pins it in `worker/package.json`. Use that
  copy (`npx wrangler` from `worker/`) so the rate-limit binding matches.

```bash
cd worker
npm ci
```

## Log in, or use a scoped API token

Either:

```bash
npx wrangler login
```

or create an API token and export it in the shell only.
Do not commit it, do not put it in `wrangler.toml`, and do not put it in the
CI workflow. The deploy Action reads `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID` from the GitHub environment `production` on that one
step. It never prints them and it never runs `wrangler secret put`.

Minimum permissions:

| Permission | Level | Why |
|---|---|---|
| Account / Workers Scripts | Edit | `wrangler deploy` uploads the script and applies the Durable Object migration |
| Account / Workers AI | Read | Attach the Workers AI binding. If deploy rejects the binding, add Workers AI Edit on the same account |
| Zone / Workers Routes | Edit | Attach the custom-domain route |
| Zone / DNS | Edit | Create the custom-domain record on the first deploy |
| Account / Workers Tail | Read | Optional. Only for `npx wrangler tail` |

The token does not need Workers KV or Logpush. Durable Objects on the Free
plan are covered by Workers Scripts Edit. Leave the account id out of the
repo. Wrangler reads it from `wrangler login` or from `CLOUDFLARE_ACCOUNT_ID`
in the environment.

`workers_dev` is false and `preview_urls` is false. The route in
[`worker/wrangler.toml`](../worker/wrangler.toml) is the custom domain
`apotheke-autofill-service.webapp.gr`. A fork changes that pattern to its own
`<your-domain>` and does not reuse this hostname.

## Configure the caps

These are plain vars in [`worker/wrangler.toml`](../worker/wrangler.toml),
not secrets. Change them and deploy again. Lowering the neuron budget is how
to test exhaustion without spending the free allocation:

| Var | Default | Meaning |
|---|---|---|
| `AUTOFILL_DAILY_NEURON_BUDGET` | `6000` | Hard daily cap. Over it, `POST /v1/suggest` is `503` with `Retry-After` until 00:00 UTC |
| `AUTOFILL_DAILY_REQUEST_CAP` | empty | Optional count of admitted suggest calls per UTC day. Empty or `0` means no request cap |
| `MAX_UPLOAD_BYTES` | `1048576` | Whole request body, 1 MiB. The enforced image-part cap is this minus 64 KiB (983040). `/v1/info` advertises `min(524288, this minus 64 KiB)`, so 524288 at this default. That advertised number is not the 413 threshold |

There is no KV namespace to create. Deploy creates the SQLite Durable Object
class `Budget` from the `migrations` block (`new_sqlite_classes`). One
instance, `idFromName("global")`, reserves neurons before each model call so
parallel requests cannot overshoot the cap.

`namespace_id` on the rate-limit binding is an integer you choose, unique
among rate-limit bindings on the account. `1001` is the docs example, not an
account id. Bindings that share an id share counters.

## Set the bearer token

Generate it without writing it into the shell history if you can, and never
commit it:

```bash
openssl rand -hex 32 | npx wrangler secret put AUTOFILL_TOKEN
```

`wrangler secret put` stores the secret on the Worker. It is not in
`wrangler.toml`. For local `wrangler dev` only, copy
[`worker/.dev.vars.example`](../worker/.dev.vars.example) to `worker/.dev.vars`
(gitignored) and put the same placeholder-replaced token there.

**`wrangler dev` calls the real Workers AI binding and spends Neurons.** The
test suite does not: Vitest stubs `env.AI` and refuses the real binding.
Prefer `npm test` over `wrangler dev` unless you mean to spend Neurons.

If `AUTOFILL_TOKEN` is missing or empty, `GET /healthz` is `503`
`{"status":"degraded"}` and `/v1/info` and `/v1/suggest` are `503`
`backend_unavailable`. The Worker does not run open.

## Deploy

The first deploy is by hand, from `worker/`, after `npm ci`:

```bash
npx wrangler deploy
openssl rand -hex 32 | npx wrangler secret put AUTOFILL_TOKEN
```

`wrangler deploy` creates the custom hostname. The certificate can take a
few minutes. `GET /healthz` stays failing until the certificate is ready and
the secret is set. Do this once. Later deploys keep the secret. Nothing in
CI runs `wrangler secret put`.

After that, [`.github/workflows/deploy-worker.yml`](../.github/workflows/deploy-worker.yml)
deploys on a push to `main` that changes `worker/**` or the workflow file,
and from Actions with `workflow_dispatch` on `main`. The job uses the GitHub
environment `production` (limit that environment to `main`). It runs `npm ci`,
the stubbed Vitest suite (no Neurons), then `npx wrangler deploy` with the
pinned Wrangler from `worker/package.json`. Secrets are step environment
variables on the deploy step only.

The smoke check then calls `https://apotheke-autofill-service.webapp.gr/healthz`
(200) and `POST /v1/suggest` with no token (401 only; 200 is not success).
The URL is `vars.WORKER_URL` when that GitHub variable is set, and this
hostname otherwise. A failed check prints the last HTTP status code and not
the body. It retries while the certificate is issued. It does not send a
photo. Before deploy, the workflow checks that `npx wrangler --version`
matches `wrangler` in `worker/package.json`.

The smoke check catches a bad deploy but does not undo it. To go back to the
previous version, run this from `worker/` with the same scoped token:

```bash
npx wrangler rollback
```

`wrangler rollback` keeps the secrets. List versions with
`npx wrangler deployments list` first if you need a specific one.

The CI workflow stays a dry run (`npx wrangler deploy --dry-run`, no token).
It does not deploy.

A fork sets its own `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` in its
own `production` environment, and sets `routes` in `wrangler.toml` to
`<your-domain>`. This repository's example is
`apotheke-autofill-service.webapp.gr`.

## Point Apotheke at it

1. Base URL: `https://apotheke-autofill-service.webapp.gr` (no path). A fork
   uses `https://<your-domain>`.
2. Token: the `AUTOFILL_TOKEN` secret.
3. Apotheke reads `max_image_bytes` from `GET /v1/info` and steps the analysis
   copy down until the multipart body fits. At the default body cap that
   advertised value is 524288 (`min(524288, MAX_UPLOAD_BYTES − 64 KiB)`). A
   lower `MAX_UPLOAD_BYTES` below 589824 lowers the advertisement to the
   enforced image cap. The Worker still accepts a larger body, up to 1048576,
   when the image part is at most 983040. To change the 524288 constant, edit
   `ADVERTISED_MAX_IMAGE_BYTES` in `worker/src/multipart.ts` and the deliberate
   pin in `worker/test/budget.test.ts`. The local runtime check reads the
   constant, and Deploy Worker runs that check before `wrangler deploy`.

`POST /v1/suggest` is multipart, the same fields as the Python service
(`image`, optional `context`). Type is decided from magic bytes.

## Rotate the token

```bash
openssl rand -hex 32 | npx wrangler secret put AUTOFILL_TOKEN
```

The new secret is live on the next version. The previous token starts
getting `401`. Update Apotheke in the same step.

## Neuron usage

`GET /v1/info` (bearer required) returns the Python fields plus:

```json
{
  "name": "cloudflare-workers",
  "version": "1",
  "capabilities": ["name", "category", "tags", "description"],
  "backend": "cloudflare-workers",
  "backend_used": "cloudflare-workers",
  "max_image_bytes": 524288,
  "ready": true,
  "budget": {
    "neurons": {"limit": 6000, "used": 0, "reserved": 0, "remaining": 6000},
    "requests": {"limit": null, "used": 0, "remaining": null},
    "resets_at": "2026-10-20T00:00:00.000Z"
  }
}
```

`used` is settled usage. `reserved` is the in-flight hold (30 Neurons per
model call, including a repair). `remaining` subtracts both and is not
reduced by another 30. `ready` is false when `remaining` is below 30, when
the day is exhausted, or when a request cap has nothing left. A remaining of
exactly 30 is still ready, and one suggest can succeed.

The Cloudflare dashboard (Workers AI, Neurons) shows the account pool. The
free allocation is 10,000 Neurons per UTC day for the whole account, shared
with every other Worker. This service stops itself at 6,000. Workers AI
internal code 3036 (daily free allocation already used, often as HTTP 429)
is `503` with `Retry-After` until 00:00 UTC, and it marks the Durable Object
exhausted. Code 3040 (out of capacity) is `503` with `Retry-After: 30` and
the message `the model is out of capacity`. It does not mark the day
exhausted. The code is read from `code`, `internalCode`, or `error_code`,
and from the error message when it starts with `3036: `, `3040: `, or
`3006: ` (optionally prefixed by `InferenceUpstreamError: `). The error
name itself is not checked.
A message that merely contains those digits is not mapped.
`lastRequestInternalStatusCode` is not read, because it is shared across
requests. Neither response includes upstream text. 3006 (request too large)
is `413` and names the enforced image cap (983040 at the default body cap),
not the advertised `max_image_bytes`.

Each call reserves 30 Neurons, then settles `usage.neurons` when the binding
returns it. Otherwise it charges Gemma 4 rates, 9091 Neurons per million
input tokens and 27273 per million output tokens. With neither, it charges
30. A repair is a second call and counts. Actual usage can settle a little
above the reservation; concurrent admissions cannot, because they take the
reservation on one Durable Object.

## Free-tier limits

| Limit | What we do |
|---|---|
| Workers Free CPU, about 10 ms per request | Body cap 1 MiB. No image re-encode. Base64 once, no extra JSON copy of the data URI. See the measurement below |
| Workers AI, 10,000 Neurons/day for the account | Cap at 6,000. Fail closed if the Durable Object errors |
| Workers KV free, 1,000 writes/day, eventually consistent | Not used |
| Rate limiting binding | 10 requests / 60 s per `CF-Connecting-IP`, after auth, on `POST /v1/suggest` only. `429` with `Retry-After: 60`. Per location and loosely enforced. The Durable Object is the real cap |
| Durable Objects on Free | One SQLite class, one instance. The counter rolls forward at UTC midnight and stays put when the clock steps backwards |
| Custom domain | `apotheke-autofill-service.webapp.gr`. A fork sets `<your-domain>`. Preview URLs and Logpush stay off |

## CPU and base64

Measured in workerd by `worker/test/base64-bench.test.ts` (`performance.now`,
1 ms resolution in that run):

| Bytes | `toBase64` median | `JSON.stringify` of the data URI | `Buffer.toString("base64")` |
|---|---|---|---|
| 1 MiB | 1 ms | 3 ms | 1 ms |
| 2 MiB | 1 ms | 5 ms | 5 ms |

These are wall-clock times around those calls, not the Workers CPU meter.
The live check measured `cpuTime` in Workers Logs: a 2.9 MiB photo was
126 ms, photos of 30 to 95 KB were 9 to 17 ms, and a rejected 3 MiB body was
41 ms. That is why the body cap is 1 MiB rather than 2 or 3 MiB.

The Worker builds the AI input once. The data URI is not passed through
`JSON.stringify` in this code. The repair call keeps the same image object,
JSON schema, and thinking flag, and changes only the text. The AI binding
still serializes that object once per call, including the repair. Magic
bytes are checked on the first 12 bytes of the image part before the rest of
that part is accepted. `Content-Length`, and a chunked body with no length,
are rejected against the body cap before the multipart parse.

Each request logs one JSON line: status, `base64_ms`, `parse_ms`,
`validate_ms`, `total_ms`, neurons, model, and how many model calls ran. It
does not log the token, the image, or brand, model number, or serial values.
The `*_ms` fields are `Date.now()` deltas. On the production isolate that
clock does not move during CPU, so a CPU-only slice can be 0. `total_ms`
includes the model await. Workers Logs `cpuTime` and `wallTime` are the
platform measurement. Trust `cpuTime` over `base64_ms`.

`npx wrangler tail` prints the request log. The Workers dashboard invocation
detail shows CPU time.

## Privacy

The photo, and any brand, model number, or serial the model reads, are sent
to Cloudflare Workers AI (`@cf/google/gemma-4-26b-a4b-it`). They are not
written to the Worker logs. The Durable Object stores only counters.

Workers Logs store the request metadata Cloudflare attaches, including
`cf-connecting-ip` and `x-real-ip`. This Worker's `console.log` line does
not add those headers, and it does not log a client address. The platform
log still keeps the connecting IP. Do not copy a real address into the repo.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `GET /healthz` is `503` `{"status":"degraded"}` | `AUTOFILL_TOKEN` is missing or empty. `npx wrangler secret put AUTOFILL_TOKEN` |
| `401` | Wrong or missing `Authorization: Bearer` token |
| `404` | Unknown path. This is 404 even when `AUTOFILL_TOKEN` is missing. `/healthz`, `/v1/info`, and `/v1/suggest` are the only routes |
| `413` | Body larger than `MAX_UPLOAD_BYTES` (the message names that cap), including a chunked body with no `Content-Length`. An image part over the enforced image cap (body cap minus 64 KiB, 983040 at the default), or Workers AI code 3006, names that image cap. Advertised `max_image_bytes` (524288) is not this cap |
| `415` | Bytes are not JPEG, PNG, or WebP. Checked from the first bytes of the image part |
| `429` `Retry-After: 60` | Per-location rate limit. Wait. The daily cap is separate |
| `503` `daily capacity is exhausted` plus `Retry-After` until 00:00 UTC | Neuron budget, request cap, or Workers AI 3036. 3036 marks the day exhausted. `GET /v1/info` shows `remaining` |
| `503` `the model is out of capacity` plus `Retry-After: 30` | Workers AI 3040. The day is not marked exhausted |
| `503` `the model is unavailable` | Workers AI returned some other HTTP 4xx. The client does not see the upstream body |
| `503` `model output was not a JSON object` | The reply was still prose after the one repair. It is not used as a name |
| `503` and the model was not called | Durable Object error. The Worker fails closed and does not serve uncapped |
| Suggest works in tests but deploy spends Neurons | Tests stub `env.AI`. Production calls Workers AI |

Model id: `@cf/google/gemma-4-26b-a4b-it`. `backend` and `backend_used` are
`cloudflare-workers`. `model_used` on a suggestion is the model id.
Thinking is off (`chat_template_kwargs.enable_thinking` false) and the reply
is constrained with JSON schema. One repair call is allowed when the first
reply is not usable, including a length-truncated reply the local repair
cannot close.
