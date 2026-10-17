# Benchmarks: Jetson Xavier NX

This page records **one benchmark session** on a Jetson Xavier NX, run on 2026-10-17/18 (all times Europe/Athens,
UTC+3). These numbers are a snapshot of that session; the page is not regenerated automatically.

**Result:** the service ships with **`qwen2.5vl-jetson:3b`** (a locally patched `qwen2.5vl:3b`, see
[Building qwen2.5vl-jetson:3b](#building-qwen25vl-jetson3b)). Through the service on build `2252ef5` it got the category
right on **9/9** test photos in a median **13.1 s** (max 13.8 s). Moondream got **4/9** in a median **4.6 s**.
Decision rule: category accuracy first, then speed. Qwen had to answer within the 25 s backend timeout at 1024 px and
fit in memory, and Moondream would have won if it came within one photo of Qwen. Qwen led by five photos.

Scripts are in [`bench/`](../bench), photos in [`bench/photos/`](../bench/photos) (with
[sources and licenses](../bench/photos/SOURCES.md)), raw outputs in [`bench/results/`](../bench/results).

## Setup

| | |
|---|---|
| Board | NVIDIA Jetson Xavier NX Developer Kit, hostname `xavier12`, root filesystem on NVMe |
| OS | L4T R35.6.0 (JetPack 5), kernel 5.10.216-tegra |
| Power | `nvpmodel` mode 8, `MODE_20W_6CORE` (the NX's fastest mode; mode 0 on the NX is a slower 15 W 2-core mode). `jetson_clocks` off unless stated |
| Memory | 6.8 GB RAM shared between CPU and GPU, plus swap: 6 × 569 MB zram and an 8 GB swap file on NVMe |
| Network | All traffic coming into the board is capped at 30 Mbit/s (tc ingress policing; the kernel has no IFB). Test clients ran on the board itself (loopback), so the cap does not affect the service timings below; it only affects uploads from other machines (see the end-to-end section) |
| Docker | 24.0.7, default runtime `nvidia` |
| Ollama | 0.15.6 (`ollama -v`), host systemd service on port 11434, `OLLAMA_CONTEXT_LENGTH=8192`. Added for the service: `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1` (and `OLLAMA_FLASH_ATTENTION=1`, which has no effect here: Ollama logs "flash attention enabled but not supported by gpu" for this compute 7.2 GPU) |
| Service | `apotheke-autofill:jetson` built from `Dockerfile.jetson`, host network, `--restart unless-stopped`, env file with `AUTOFILL_TOKEN`, `OLLAMA_MODEL`, `OLLAMA_KEEP_ALIVE=-1`. Builds used: **`2252ef5`** (main results), `d3f8c3c` (earlier results, labelled) |
| Generation | `num_predict` 256, temperature 0 |

Models (`ollama list` ID, and the sha256 of the GGUF weights blob):

| Model tag | ID | Weights blob | Size | Notes |
|---|---|---|---|---|
| `moondream:latest` | `55fc3abd3867` | `sha256:e554c6b9de01…` | 1.7 GB | Moondream 2, loads as 1.3 GB on the GPU (ctx 2048) |
| `qwen2.5vl:3b` | `fb90415cde1e` | `sha256:e9758e589d443f653821b7be9bb9092c1bf7434522b70ec6e83591b1320fdb4d` | 3.2 GB | stock; **does not run on this board** (see below) |
| `qwen2.5vl-jetson:3b` | `c917b94b8c33` | `sha256:04f642ba7f1170bb3168f1304f112610e02d9199895b0a534c05dadb8041aed8` | 3.2 GB | stock weights, `vision.max_pixels` = 501760, `num_ctx` 4096; loads as 4.2 GB on the GPU (all 37 layers) |

The raw comparison in the appendix used the same weights under ID `fa39d1704d14` (before `num_ctx 4096` was added
to the Modelfile, so it ran at ctx 8192).

## Method

- **Photos:** 9 plain single-product photos from Wikimedia Commons ([sources](../bench/photos/SOURCES.md)),
  shrunk to at most 1024 px on the long side (JPEG quality 85, 27–168 KB). That matches what Apotheke sends: it always
  re-encodes to a ≤1024 px JPEG.
- **Context:** every request included category context with the 9 categories `Tools, Electronics, Clothing, Books,
  Kitchen, Furniture, Lighting, Shoes, Other`. "Correct" means the returned category equals the expected one in
  SOURCES.md.
- **Service passes** ([`bench/svcpass.sh`](../bench/svcpass.sh)): `POST /v1/suggest` on the board's loopback, one
  request at a time, **one run per photo per model**. Before each model the container was recreated with that
  `OLLAMA_MODEL`, the script waited for `/v1/info` `ready: true`, then sent one warm-up request with a photo outside
  the test set (so no measured photo hits Ollama's prompt cache). Times are curl `time_total`.
- **Raw comparison** ([`bench/cmp2.sh`](../bench/cmp2.sh) + [`bench/rawbench.py`](../bench/rawbench.py)): straight to
  Ollama `/api/chat` with the service's prompt and parser, autofill container stopped, **Ollama restarted between
  models** so the previous runner's memory was freed, one run per photo, peak RAM from `tegrastats`.
- **Isolation:** nothing else was sending requests to the service or Ollama during the runs listed here, except where
  noted. (An unrelated container on the board was crash-looping throughout; it does not use Ollama.)
- **Token handling:** scripts read the token from a mode-600 header file with `curl -H @file`, so it never appears on a
  command line or in `ps`.

## Model comparison (service, build 2252ef5)

Build `2252ef5` sends a JSON schema as Ollama's `format`, with the category as an `enum` of the request's categories.
Raw responses: [`service-2252ef5_qwen2.5vl-jetson.jsonl`](../bench/results/service-2252ef5_qwen2.5vl-jetson.jsonl),
[`service-2252ef5_moondream.jsonl`](../bench/results/service-2252ef5_moondream.jsonl). Measured 2026-10-18 00:44–00:48.

### qwen2.5vl-jetson:3b

| Photo | HTTP | Time (s) | Name (confidence) | Category | Description |
|---|---|---:|---|---|---|
| cordless_drill | 200 | 13.75 | cordless drill (0.9) | Tools ✓ | A cordless drill with a blue body and a yellow battery cover. |
| desk_lamp | 200 | 12.27 | blue light (0.9) | Lighting ✓ | a blue light with a long arm |
| hammer | 200 | 13.13 | hammer (1.0) | Tools ✓ | A hammer with a metal head and an orange handle. |
| headphones | 200 | 13.67 | headphones (0.9) | Electronics ✓ | A pair of Bose headphones with a carrying case. |
| kitchen_knife | 200 | 12.64 | knives (1.0) | Kitchen ✓ | Three knives with black handles and shiny metal blades. |
| laptop | 200 | 13.29 | laptop (0.9) | Electronics ✓ | a black laptop with a screen and keyboard |
| paperback_book | 200 | 13.07 | book (0.9) | Books ✓ | A stack of white paper books on a wooden surface. |
| sneakers | 200 | 13.05 | sneaker (1.0) | Shoes ✓ | A white sneaker with red accents. |
| t-shirt | 200 | 13.74 | T-shirt (0.9) | Clothing ✓ | A dark grey T-shirt with a white and blue text design. |

**9/9 correct, median 13.13 s, max 13.75 s.**

### moondream:latest

| Photo | HTTP | Time (s) | Name (confidence) | Category | Description |
|---|---|---:|---|---|---|
| cordless_drill | 200 | 4.63 | drill (0.67) | Electronics ✗ (expected Tools) | A blue and black drill with a yellow trigger |
| desk_lamp | 200 | 4.68 | blue desk lamp (0.67) | Electronics ✗ (expected Lighting) | A blue desk lamp with a white shade and silver arm. |
| hammer | 200 | 4.38 | hammer (0.67) | Tools ✓ | A hammer with a red handle and grey head. |
| headphones | 200 | 6.06 | Black headphones (0.5) | Electronics ✓ | A pair of black headphones with a silver ear cup and a black headband. The headphones have a white logo on the left side, which is the Bose logo. |
| kitchen_knife | 200 | 4.49 | Knife (0.67) | Kitchen ✓ | A knife with a black handle and silver blade. |
| laptop | 200 | 5.15 | Black laptop computer (0.5) | Tools ✗ (expected Electronics) | A black laptop computer with a silver logo on its lid. The screen shows that it is open to a web page. |
| paperback_book | 200 | 4.31 | Book laying (0.5) | Books ✓ | A book laying on a wooden floor. |
| sneakers | 200 | 4.56 | White and red sneaker (0.5) | Clothing ✗ (expected Shoes) | A white and red sneaker with a black sole. |
| t-shirt | 200 | 5.24 | Black t-shirt (0.5) | Books ✗ (expected Clothing) | A black t-shirt with a white block that says 'i edit wikipedia' on it. |

**4/9 correct, median 4.63 s, max 6.06 s.**

### Summary

| | qwen2.5vl-jetson:3b | moondream:latest |
|---|---|---|
| Category accuracy (2252ef5, service) | **9/9** | 4/9 |
| Median / max time per photo (service, model loaded) | 13.13 s / 13.75 s | 4.63 s / 6.06 s |
| Generation speed (raw run, appendix) | ~16 tok/s | ~33 tok/s |
| Prompt tokens (image + text, raw run) | 777–806 | 914 |
| Loaded size (`ollama ps`) | 4.2 GB at ctx 4096 | 1.3 GB at ctx 2048 |
| Peak board RAM (`tegrastats`) | 5896 MB of 6833 through the service (ctx 4096, d3f8c3c); 6076 MB in the raw run (ctx 8192) | 4027 MB in the raw run |

Peak RAM was not recorded during the 2252ef5 passes. The figures are from the d3f8c3c runs with the same models.
With Qwen loaded permanently the board has about 0.8–1 GB available.

Observations:

- With the schema, Moondream's answers are always well-formed JSON and its names and descriptions are sensible (it no
  longer copies the prompt's template back, QA's S1). Its category choice is still weak: drill and lamp → Electronics,
  laptop → Tools, sneaker → Clothing, t-shirt → Books.
- Qwen's names are generic and its descriptions accurate. Weak spots: the desk lamp is named "blue light", and the
  paperback (a coverless stack of pages) is described as "a stack of white paper books".

### Why stock `qwen2.5vl:3b` fails on this board

With the stock model, the first request ran into the client's 600 s timeout and the board peaked at 6677 MB of 6833.
Ollama's log shows why:

- At load, Ollama's runner reserves memory for the largest image the model accepts. It does so by encoding a
  2048×2048 test image, capped by the model's `vision.max_pixels`. Ollama's default for Qwen 2.5 VL is 2 MP
  (`2<<20`), about 10,700 vision patches.
- The vision encoder's full-attention layers then need a single **7.3 GiB CUDA buffer**
  (`ggml_backend_cuda_buffer_type_alloc_buffer: allocating 7328.38 MiB on device 0: cudaMalloc failed: out of memory`).
  That is more than the board's total RAM. Flash attention would shrink it, but Ollama does not support it on this
  GPU (compute 7.2).
- Ollama then retries with fewer and fewer GPU layers (37 → 0, about 1.3–11 s per attempt), and finally runs on the CPU.
  That takes far longer than any client timeout.
- Shrinking the photos does not help: the reservation is sized for the model's maximum image, not the image sent.
- An unrelated factor made things worse that night: the long-running Ollama process was holding about 3 GB even with
  no model loaded. Restarting Ollama freed it (`free` went from 3.4 GB to 0.4 GB used).

### Building qwen2.5vl-jetson:3b

The fix is to lower `vision.max_pixels` in the GGUF's metadata. Weights, template and parameters stay the same.
With `qwen25vl.vision.max_pixels = 501760` (= 28 × 28 × 640, about 0.5 MP) the worst-case vision buffer drops to a few
hundred MB. All 37 layers then fit on the GPU, and loading takes about 15 s. A 1024×768 photo is scaled to about 0.5 MP
inside Ollama before encoding, which kept category accuracy at 9/9 on this set.

Recipe (on the board, as root; [`bench/qwen2.5vl-jetson/`](../bench/qwen2.5vl-jetson)):

```bash
ollama pull qwen2.5vl:3b
# 1. Locate the stock GGUF (the "model" layer of the manifest).
BLOB=$(python3 -c 'import json;m=json.load(open("/usr/share/ollama/.ollama/models/manifests/registry.ollama.ai/library/qwen2.5vl/3b"));print([l["digest"] for l in m["layers"] if l["mediaType"].endswith(".model")][0].replace(":","-"))')
# 2. Copy it with the extra key. Needs Python 3.8+ with the gguf package; any Python works, here the service image's.
mkdir -p /tmp/qwen-jetson && cp bench/qwen2.5vl-jetson/* /tmp/qwen-jetson/
docker run --rm -v /usr/share/ollama/.ollama/models/blobs:/blobs:ro -v /tmp/qwen-jetson:/w -w /w \
  --entrypoint bash apotheke-autofill:jetson -c \
  "python3.11 -m pip install -q --target /tmp/pp gguf numpy && \
   PYTHONPATH=/tmp/pp python3.11 gguf_set_maxpix.py /blobs/$BLOB qwen2.5vl-3b-maxpix.gguf 501760"
# 3. Create the model from the copy (Modelfile = stock template/system/params + num_ctx 4096).
cd /tmp/qwen-jetson && ollama create qwen2.5vl-jetson:3b -f Modelfile
ollama show qwen2.5vl-jetson:3b      # Parameters: num_ctx 4096, temperature 0.0001
rm qwen2.5vl-3b-maxpix.gguf          # Ollama keeps its own copy as a blob
```

The patched blob is 32 bytes larger than the stock one (3,200,614,752 vs 3,200,614,720 bytes). Its sha256 is
`04f642ba…aed8` (full value in [Setup](#setup)). The result should be byte-identical given the same stock blob and
gguf version. A different gguf version may order things differently, so compare `ollama show` output, not the digest.

Board settings that go with it: `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1` in a systemd drop-in for
`ollama.service`, so a second model or a second parallel slot cannot push Qwen off the GPU. As a side effect, any other
user of the board's Ollama who loads a different model evicts Qwen, and the service then reloads it (see the cold check
below).

## Final deployment

- Deployed on xavier12 on 2026-10-18 at `main` **7339211** (merge of PR #7; app code identical to e4e9033), image `apotheke-autofill:jetson-7339211`, model `qwen2.5vl-jetson:3b` with `OLLAMA_KEEP_ALIVE=-1`, `docker run --restart unless-stopped --env-file /opt/autofill/service.env`.
- `BACKEND_TIMEOUT_S=25` until Apotheke v2.3 ships (keeps v2.2.0 clients on the fast-503 path), then raise it to 45.
- verify.sh: healthz ok; info ready with avg_latency_ms; cold after `ollama stop` → 503 in 0.08 s (Retry-After 15); two at once → second 503 in 0.03 s (Retry-After 14).
- Reboot test: container, Ollama (Qwen reloaded), dl-cap, GitHub runner and WireGuard reachability all came back; healthz ok, info ready, suggest 200.
- Jetson hardware workflow (#4) on main 7339211, Moondream smoke: [run 39076265664](https://github.com/thordin9/apotheke-autofill-service/actions/runs/39076265664) — success, 16/16 ok, p50 2.89 s, p95 5.59 s, concurrent pair [200, 503].

## Service results

### Qwen 100-run (build 2252ef5, 1024 px photos)

Run on 2026-10-18 00:56–01:19 with [`bench/bench100.sh`](../bench/bench100.sh): 100 sequential `POST /v1/suggest`
requests cycling the 9 photos, 9 categories as context, model loaded, nothing else using the service, 20 W,
`jetson_clocks` off, `tegrastats` every 0.5 s. Service image `apotheke-autofill:jetson-2252ef5`
(`BACKEND_TIMEOUT_S` 25). Raw CSV: [`bench100-2252ef5_qwen2.5vl-jetson_20w.csv`](../bench/results/bench100-2252ef5_qwen2.5vl-jetson_20w.csv).

| Run | Requests | 200s | Mean | p50 | p95 | Max | Peak RAM | Max GPU temp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| qwen2.5vl-jetson:3b, 20 W (2252ef5) | 100 | 100 | 13.16 s | 13.32 s | 13.80 s | 14.43 s | 5951 MB | 63 °C |

No errors or retries; every request finished well inside the 25 s backend timeout.

### Moondream 100-run (d3f8c3c, old photo set)

Run on 2026-10-17 at about 23:30–23:45, **before** the photo set was replaced and the schema was added, so it measures
speed, not answer quality. The old set had 4 photos that weren't the named product, and some were larger than 1024 px.
Context had 8 categories (no Furniture). Raw CSVs:
[20 W](../bench/results/bench100-d3f8c3c_moondream_20w_oldphotos.csv),
[20 W + jetson_clocks](../bench/results/bench100-d3f8c3c_moondream_20w-jetson_clocks_oldphotos.csv).

| Run | Requests | 200s | Mean | p50 | p95 | Max | Peak RAM | Max GPU temp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| moondream, 20 W | 100 | 100 | 4.84 s | 4.97 s | 6.83 s | 7.03 s | 4266 MB | 58 °C |
| moondream, 20 W + `jetson_clocks` | 100 | 100 | 4.80 s | 4.90 s | 6.77 s | 7.28 s | 4260 MB | 57 °C |

`jetson_clocks` made no meaningful difference, so the board runs without it.

### Cold start and recovery (2252ef5, qwen2.5vl-jetson:3b)

Checked with [`bench/verify.sh`](../bench/verify.sh) at about 00:48 on 2026-10-18:

| Step | Result |
|---|---|
| `/healthz` | `{"status":"ok"}` |
| `/v1/info` | `ollama-qwen2.5vl-jetson:3b`, `ready: true` |
| One suggest (hammer) | 200 in 13.0 s, Tools (confidence 1.0) |
| `ollama stop qwen2.5vl-jetson:3b`, then `/v1/info` | `ready: false` |
| First suggest while unloaded | **503 `backend_unavailable` in 0.09 s, `Retry-After: 15`** ("model … is loading"); the service starts a reload |
| `/v1/info` ready again | after about 10 s |
| Retry | 200 in 19.0 s (laptop, Electronics). The first request after a load is about 5 s slower than later ones |

Model load time: about 15 s for Qwen (Ollama `load_duration` 14.7 s cold), about 11 s for Moondream.
The service sets `keep_alive=-1` and warms up at start, so the model is normally resident. A cold model happens only if
Ollama restarts by itself or another model evicts Qwen.

Before the fix (d3f8c3c, keep-alive 30 min): the first request after `ollama stop` waited for the load and failed with
**504 `backend_timeout` after 25.0 s** (load about 15 s plus about 13.5 s inference).

### Queue behaviour (2252ef5)

| Case | Result |
|---|---|
| Two requests at once | First: 200 in 13.7 s. Second: **503 `backend_unavailable` in 0.03 s, `Retry-After: 14`** ("backend busy, a queued request would time out") |

Before the fix (d3f8c3c): the second request queued behind the first and got **504 `backend_timeout` at 25.0 s**,
because two Qwen inferences (about 2 × 13.5 s) do not fit in the 25 s backend timeout.

### Known issues and open items

- **Apotheke client** (QA's A1–A3, for Coder): Apotheke shows "busy or not ready" for every 503 and ignores
  `Retry-After`. Low-confidence fallback suggestions are filled in like confident ones. Ownership of "new category"
  suggestions is undecided, because the service drops categories that aren't in the list.
- **Retry-After on `backend_error`** (QA's S3): not re-checked on 2252ef5.
- **Shared Ollama:** with `OLLAMA_MAX_LOADED_MODELS=1`, anyone loading another model on the board's Ollama evicts Qwen.
  The next service request then gets a 503 with `Retry-After` while it reloads (about 15–20 s).
- **Memory headroom:** about 0.8–1 GB available with Qwen loaded.
- **Apotheke's stock categories** (`Documents, Electronics, General, Hobby, Kitchen, Tools`) lack Lighting, Books,
  Shoes and Clothing, so in the end-to-end test some photos landed in the closest category, or in Hobby or General.

## Reproduce

Run by hand on the board, or use the **Jetson hardware** workflow from PR #4
(`.github/workflows/jetson-hardware.yml`), which only runs by hand (`workflow_dispatch`) on the self-hosted `jetson`
runner. There is no nightly or push trigger. That workflow runs its own smoke test on its fixture photos. The tables on
this page come from the scripts below.

Make sure nobody else is using the service or Ollama: `verify.sh`, `coldwarm.sh` and `cmp2.sh` unload models, and
`svcpass.sh` recreates the service container.

```bash
git clone https://github.com/thordin9/apotheke-autofill-service && cd apotheke-autofill-service
git checkout 2252ef5
docker build -f Dockerfile.jetson -t apotheke-autofill:jetson .

# Token header file (mode 600) from the service env file; the token is never printed.
sudo bench/make-auth-header.sh /opt/autofill/service.env /opt/autofill/auth.hdr

# Build qwen2.5vl-jetson:3b: see "Building qwen2.5vl-jetson:3b" above.

# Service passes (one per model), then put the service back on the chosen model.
export WARMUP_PHOTO=/path/to/a/photo-not-in-bench-photos.jpg
sudo -E bench/svcpass.sh moondream apotheke-autofill:jetson moondream
sudo -E bench/svcpass.sh qwen2.5vl-jetson:3b apotheke-autofill:jetson qwen
docker rm -f autofill && docker run -d --name autofill --network host --runtime nvidia \
  --restart unless-stopped --env-file /opt/autofill/service.env apotheke-autofill:jetson

sudo bench/verify.sh                       # health, info, cold recovery, two at once
sudo bench/bench100.sh qwen-2252ef5         # 100 sequential requests; prints mean/p50/p95/max, peak RAM, GPU temp

# Raw comparison straight against Ollama (stops/restarts Ollama; stop the service container first):
docker stop autofill && sudo bench/cmp2.sh apotheke-autofill:jetson moondream qwen2.5vl-jetson:3b; docker start autofill
```

Results go to `bench/out/` (git-ignored). Override `AUTOFILL_DIR`, `AUTH_HEADER_FILE`, `PHOTOS`, `OUT` or
`SERVICE_URL` from the environment if your layout differs (see [`bench/common.sh`](../bench/common.sh)).

The next section is QA's report, pasted verbatim. It was measured from Apotheke v2.2.0 over the WireGuard tunnel
against **build 2252ef5 with qwen2.5vl-jetson:3b**, and it lists upload time separately.

## End-to-end from Apotheke

Measured on 2026-10-18, 00:50–00:55 Europe/Athens (UTC+3), with no other traffic on the service. Each number is a single run.

### Setup
| | |
|---|---|
| Apotheke | v2.2.0 (`e3baae4`), stock categories `Documents, Electronics, General, Hobby, Kitchen, Tools` |
| Service | `apotheke-autofill:jetson-2252ef5`, built from PR #3 `2252ef5` |
| Model (`/v1/info`) | `ollama-qwen2.5vl-jetson:3b`, backend `ollama`, `max_image_bytes` 10485760, `ready: true`. 100 % GPU, `OLLAMA_KEEP_ALIVE=-1` |
| Board | Jetson Xavier NX, L4T R35.6.0, `MODE_20W_6CORE`, `jetson_clocks` off |
| Network path | Apotheke host → WireGuard tunnel (~20 Mbit/s measured upload) → board, with a 30 Mbit/s cap on traffic coming into the board |
| Photos | the 9 product photos listed in SOURCES.md |

Apotheke re-encodes every photo to a ≤1024 px JPEG (quality 82) before sending it. The tunnel is the worst case. On the LAN, uploads run at the 30 Mbit/s cap, so the "LAN" column swaps the tunnel upload time for size ÷ 30 Mbit/s.

### Typical photo (≤1024 px JPEG as Apotheke sends it), model loaded
Times are in seconds. "Service" also includes Apotheke's own processing (about 0.1 s) and the ~0.15 s connection setup is subtracted. Total is what Apotheke's `/autofill/suggest` took.

| Photo | Sent | Upload | Service | Total via Apotheke | LAN total (est.) | Suggestion |
|---|---:|---:|---:|---:|---:|---|
| cordless_drill | 99 KB | 0.04 | 14.81 | 15.00 | 14.99 | cordless drill · Tools |
| desk_lamp | 26 KB | 0.01 | 13.47 | 13.63 | 13.63 | blue light · Electronics |
| hammer | 35 KB | 0.01 | 13.51 | 13.68 | 13.67 | hammer · Tools |
| headphones | 88 KB | 0.04 | 14.00 | 14.18 | 14.17 | headphones · Electronics |
| kitchen_knife | 28 KB | 0.01 | 13.02 | 13.18 | 13.18 | knives · Kitchen |
| laptop | 137 KB | 0.06 | 14.12 | 14.33 | 14.31 | laptop · Electronics |
| paperback_book | 159 KB | 0.07 | 13.53 | 13.74 | 13.72 | book · Documents |
| sneakers | 95 KB | 0.04 | 14.03 | 14.22 | 14.21 | sneaker · General |
| t-shirt | 149 KB | 0.06 | 13.96 | 14.17 | 14.15 | T-shirt · Hobby |
| **p50 / max** | | **0.04 / 0.07** | **13.96 / 14.81** | **14.17 / 15.00** | **14.15 / 14.99** | |

Names are right on 8 of 9 photos. Categories are right or the closest available on 8 of 9: the stock list has no Lighting, Books, Shoes or Clothing, and the t-shirt was put in Hobby.

### Large photo and cold start
| Case | Upload | Service | Total | LAN total (est.) |
|---|---:|---:|---:|---:|
| 5600×4200 JPEG, 9.1 MiB, sent straight to the service | ~3.9 | ~16.7 | 20.8 | ~19.4 (upload 2.55) |
| Same photo via Apotheke (downscaled to ~80 KB) | <0.05 | – | 14.4 | ~14.4 |
| Model unloaded: first request | – | – | 503 in 0.6 s, `Retry-After: 15` | – |
| Retry after Retry-After (model loading in the background) | <0.05 | – | 16.4 | ~16.4 |

Every case through Apotheke stays well inside the 30 s client timeout (worst 16.4 s). A full-size photo sent straight to the service uses 16.7 s of the 25 s backend timeout.

### Concurrency
| Case | Result |
|---|---|
| 2 at once | first 200 (≈14 s). Second **503 `backend_unavailable` in ~0.8 s**, `Retry-After: 14` ("a queued request would time out"). No 504 |
| 3 at once | 1 × 200. 2nd and 3rd 503 in ~0.75 s, `Retry-After: 14` |

### Failure handling
| Case | Service answer | Apotheke shows |
|---|---|---|
| Wrong token | 401 in 0.3 s | "the service refused the access token" |
| Service unreachable (dead port) | – | "the autofill service is not reachable" (0.2 s) |
| Busy, or model loading | 503 + Retry-After | "the service is busy or not ready. Try again later." |
| Model loading, Test connection | `/v1/info` `ready:false` | "…still warming up (loading its model). Try again in a minute." |
| Image > 10 MiB | 413 `payload_too_large` (11.1 MB: 4.9 s) | JPEGs are downscaled first. An oversized HEIC: "larger than the service accepts" |
| HEIC (no pillow-heif) | 415 `unsupported_media_type` | "the service cannot read this kind of image" |
| Token exposure | – | not in any page, export, backup, log or error message |

## Appendix: raw comparison straight against Ollama (d3f8c3c prompt, format=json)

Measured 2026-10-18 00:28–00:32 with `bench/cmp2.sh` on the `d3f8c3c` image: its prompt (with a JSON template) and
`format: "json"`, no schema. The autofill container was stopped and Ollama was restarted before each model. The first
photo of each run includes the cold load. Raw outputs, including the model's raw text:
[`raw-d3f8c3c_moondream.json`](../bench/results/raw-d3f8c3c_moondream.json),
[`raw-d3f8c3c_qwen2.5vl-jetson.json`](../bench/results/raw-d3f8c3c_qwen2.5vl-jetson.json).
Superseded by the 2252ef5 comparison above. Kept for tok/s, load times and peak RAM.

**moondream:latest:** 5/9 correct, median 4.38 s (4.23 s once loaded), about 33 tok/s, peak RAM 4027 MB

| Photo | Time (s) | Load (s) | tok/s | done_reason | Name | Category |
|---|---:|---:|---:|---|---|---|
| cordless_drill | 16.38 | 11.35 | 32.9 | stop | drill | Electronics ✗ |
| desk_lamp | 4.00 | 0.14 | 33.2 | stop | table lamp | Electronics ✗ |
| hammer | 5.18 | 0.13 | 32.3 | stop | hammer | Tools ✓ |
| headphones | 4.09 | 0.14 | 32.8 | stop | bose headphones | Electronics ✓ |
| kitchen_knife | 3.73 | 0.13 | 33.3 | stop | Knife | Kitchen ✓ |
| laptop | 4.38 | 0.13 | 32.5 | stop | smom | Electronics ✓ |
| paperback_book | 3.61 | 0.15 | 33.5 | stop | book | Books ✓ |
| sneakers | 4.94 | 0.13 | 32.4 | stop | reebok | Clothing ✗ |
| t-shirt | 17.32 | 0.14 | 30.1 | length (hit 256) | i edit wikipedia | Books ✗ |

**qwen2.5vl-jetson:3b (ctx 8192):** 9/9 correct, median 13.66 s (13.54 s once loaded), about 16 tok/s, peak RAM 6076 MB

| Photo | Time (s) | Load (s) | tok/s | done_reason | Name | Category |
|---|---:|---:|---:|---|---|---|
| cordless_drill | 29.20 | 14.73 | 15.9 | stop | drill | Tools ✓ |
| desk_lamp | 13.19 | 0.54 | 16.1 | stop | blue light | Lighting ✓ |
| hammer | 13.66 | 0.46 | 16.2 | stop | hammer | Tools ✓ |
| headphones | 13.42 | 0.40 | 16.0 | stop | headphones | Electronics ✓ |
| kitchen_knife | 12.79 | 0.37 | 16.2 | stop | knife | Kitchen ✓ |
| laptop | 13.70 | 0.37 | 16.1 | stop | laptop | Electronics ✓ |
| paperback_book | 13.79 | 0.38 | 14.0 | stop | book | Books ✓ |
| sneakers | 13.96 | 0.36 | 16.1 | stop | sneaker | Shoes ✓ |
| t-shirt | 13.16 | 0.36 | 16.2 | stop | T-shirt | Clothing ✓ |

Of Qwen's roughly 13.5 s per photo, Ollama reports about 0.4 s load, 2.4 s prompt evaluation and 3.6 s generation
(about 58 tokens). The remaining roughly 7 s is not broken out; it is presumably the vision encoder.
