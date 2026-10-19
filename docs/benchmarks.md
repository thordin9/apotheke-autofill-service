# Benchmarks: Jetson Xavier NX

This page records **two benchmark sessions** on a Jetson Xavier NX (all times Europe/Athens, UTC+3): the model
choice on 2026-10-17/18 (from [Setup](#setup) on), and an image-size sweep with brand/serial photos and a Qwen3-VL 2B
attempt on 2026-10-18 ([next section](#image-size-sweep-and-qwen3-vl-2b-2026-10-18)). The numbers are snapshots of
those sessions; the page is not regenerated automatically.

**Current recommendation (second session):** `OLLAMA_MODEL=qwen2.5vl-jetson:3b`, `IMAGE_SIZES=448,560*,1024`,
`ALLOWED_MODELS` unset. Photos sent at 560 px answer in a median 8.3 s instead of 13.9 s at 1024 px, with the same
accuracy on 19 photos. `qwen3-vl:2b` does not fit in memory with Ollama 0.15.6.

**Result of the first session:** the service ships with **`qwen2.5vl-jetson:3b`** (a locally patched `qwen2.5vl:3b`, see
[Building qwen2.5vl-jetson:3b](#building-qwen25vl-jetson3b)). Through the service on build `2252ef5` it got the category
right on **9/9** test photos in a median **13.1 s** (max 13.8 s). Moondream got **4/9** in a median **4.6 s**.
Decision rule: category accuracy first, then speed. Qwen had to answer within the 25 s backend timeout at 1024 px and
fit in memory, and Moondream would have won if it came within one photo of Qwen. Qwen led by five photos.

Scripts are in [`bench/`](../bench), photos in [`bench/photos/`](../bench/photos) (with
[sources and licenses](../bench/photos/SOURCES.md)), raw outputs in [`bench/results/`](../bench/results).

## Image-size sweep and Qwen3-VL 2B (2026-10-18)

Second session, 2026-10-18 11:50–12:40 Europe/Athens (sweep requests 12:11:31–12:36:35). It answers three questions:
how much faster smaller images make `qwen2.5vl-jetson:3b`, what that costs in accuracy (including brand names, model
numbers and serial numbers), and whether `qwen3-vl:2b` is a faster alternative.

### Recommendation

| Setting | Value | Why |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5vl-jetson:3b` (`max_pixels` 501760) | Keeps the 1024 px option available. `max_pixels` is only a ceiling; the client's resize sets the real cost. |
| `IMAGE_SIZES` | `448,560*,1024` | **560 px is the recommended default**: median **8.3 s** instead of 13.9 s at 1024 px (−40 %), with category, name and brand scores equal to 1024 px (16/19, 18/19, 7/10). 448 px is 1.1 s faster again but names drop to 15/19 (desk lamp → "umbrella", paperback → "wooden block"). 1024 px stays for labels with small print. 672 px was slower than 560 px (9.9 s, p90 12.2 s) and no more accurate, so it is left out. |
| `ALLOWED_MODELS` | *(unset)* | The lower-`max_pixels` tags give the same speed/accuracy curve as client-side resizing of the default tag (200k ≈ 448 px, 300k ≈ 560 px), but every switch is a 21–29 s cold start that evicts the default model. `qwen3-vl:2b` is installed but **must not be listed**: it cannot load on this board (below), and the service offers any installed allowlisted model. `moondream:latest` is fast (4.6 s) but got 4/9 categories in the first session. |

Accuracy differences between configurations are one or two photos out of 19, which is within what a single run per
photo can show. The speed differences are large and consistent (p90 tracks the median closely).

### Setup (both sessions unless noted)

| | |
|---|---|
| Board | NVIDIA Jetson Xavier NX Developer Kit (`/proc/device-tree/model`), NVMe root filesystem |
| L4T | R35.6.0 (`/etc/nv_tegra_release`: R35, REVISION 6.0, GCID 37391689), kernel 5.10.216-tegra (JetPack 5) |
| Power | `nvpmodel` mode 8, `MODE_20W_6CORE`; `jetson_clocks` off. During inference the GPU ran at its 1109 MHz maximum, 99 % busy (tegrastats) |
| Memory | 6833 MB RAM shared by CPU and GPU. Swap: 6 × 569 MB zram (lzo-rle, priority 5) and an 8 GB swap file on NVMe (priority −2, used only after zram) |
| Ollama | 0.15.6, host systemd service; drop-in `10-autofill.conf` with `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_FLASH_ATTENTION=1` (no effect on compute 7.2); `override.conf` with `OLLAMA_CONTEXT_LENGTH=8192` (the Modelfiles set `num_ctx 4096`) |
| Docker / image | Docker 24.0.7. Service image `apotheke-autofill:jetson-7339211` (built from `Dockerfile.jetson` at `7339211`, base `dustynv/ollama:r35.4.1`). Its `build_prompt`/`build_schema` are identical to `main` at `7f0357e` |
| Models | `qwen2.5vl-jetson:3b` (`c917b94b8c33`), `qwen2.5vl-jetson:3b-400k` (`5f293cd87579`), `:3b-300k` (`f244f99b4f33`), `:3b-200k` (`6558424fa5ab`), `qwen3-vl:2b` (`0635d9d857d4`, weights blob `sha256:ebabfa59b71a…`, 1.89 GB, Q4_K_M) |
| Other load | None: the board was reserved for this run; the service container stayed up but received no requests. The unrelated crash-looping container mentioned in the first session's [Method](#method) was removed before this session |
| Network | Requests ran on the board (loopback), so the 30 Mbit/s inbound cap does not affect any timing here |

### Method

- **Photos:** the 9 original photos plus 10 new ones with brand names, model numbers or serial numbers (rating plates,
  branded tools and electronics, medicine and lamp packaging), all from Wikimedia Commons. Sources, licenses and
  ground truth: [`bench/photos/SOURCES.md`](../bench/photos/SOURCES.md) and
  [`bench/photos/ground_truth.json`](../bench/photos/ground_truth.json).
- **Categories:** Apotheke's default list for new installs, `General, Tools, Electronics, Kitchen, Documents, Hobby,
  Clothing, Shoes, Books, Lighting` (the first session used a 9-category list with `Furniture` and `Other`, so the
  category scores of the two sessions are not directly comparable).
- **Sizes:** two ways of sending fewer pixels.
  - *Model ceiling:* tags with `qwen25vl.vision.max_pixels` = 401408, 301056 and 200704 (28 × 28 × 512 / 384 / 256),
    next to the default 501760; the photo is sent at 1024 px and Ollama shrinks it to the ceiling.
  - *Client resize:* the default tag with the photo shrunk to 672, 560 or 448 px on the long side
    ([`bench/make_sets.py`](../bench/make_sets.py), Pillow LANCZOS, JPEG quality 85). These sizes are all below the
    ceiling for most photos, so the resize sets the cost.
- **Request:** straight to Ollama `POST /api/chat`, the service's own prompt and schema imported from the service
  image (`app.backends.ollama.build_prompt` / `build_schema`), `stream: false`, `keep_alive: -1`,
  `options: {"temperature": 0, "num_predict": 256}`, one image, one request at a time. Exact prompt:

  ```text
  What is the main object in this photo? Answer in JSON with:
  name (the kind of object in 1-4 plain words, never a brand or a person's name),
  category (exactly one of: General, Tools, Electronics, Kitchen, Documents, Hobby, Clothing, Shoes, Books, Lighting),
  tags (up to 5 short lowercase tags for the item),
  description (one short sentence about the object only, not people or background),
  confidence (0 to 1, how sure you are).
  Write the values in the language of locale 'en'.
  ```

  `format` (JSON schema):

  ```json
  {"type": "object", "properties": {"name": {"type": "string", "minLength": 2, "maxLength": 40},
   "category": {"type": "string", "enum": ["General", "Tools", "Electronics", "Kitchen", "Documents", "Hobby",
   "Clothing", "Shoes", "Books", "Lighting"]}, "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
   "description": {"type": "string", "minLength": 10, "maxLength": 200}, "confidence": {"type": "number"}},
   "required": ["name", "category", "tags", "description", "confidence"]}
  ```

- **Order per configuration** ([`bench/sweep.py`](../bench/sweep.py)): unload every model (`keep_alive: 0`), wait
  until `/api/ps` is empty plus 5 s, send one **cold** request with a photo outside the set
  (`tests/fixtures/hardware/mug.jpg`), then each of the 19 photos once, sorted by name (**warm**).
- **Timings:** *wall* = time around the HTTP call in the client (Python `time.time()`), which is what the service
  would see; Ollama's own fields `load_duration`, `prompt_eval_count/duration`, `eval_count/duration` and
  `total_duration` are stored for every request. "Unreported" = `total_duration` − load − prompt eval − eval; the GPU
  is 99 % busy during it and it shrinks with the pixel count, so it is mostly the vision encoder (Ollama does not
  report it separately). "Cold" = wall time of the cold request (its `load_duration` in brackets).
- **RAM:** `tegrastats --interval 500`, each line stamped with the epoch time; peak = the highest board RAM value
  (all processes, not only Ollama) between a configuration's first request and its last. Board RAM with no model
  loaded was 631 MB.
- **Scoring** ([`bench/score.py`](../bench/score.py)), on the model's raw JSON (no service post-processing):
  category must equal the expected one (the Fluke multimeter also accepts Tools); the name must contain one of the
  photo's `name_keywords` (e.g. a desk lamp must be called a lamp, "blue light" fails). Brand, model number and serial
  are lower-cased, stripped to letters and digits and looked for in name + tags + description: *exact* = the whole
  string appears, *partial* = a common run of at least half its characters (min. 3). Note the service's prompt asks
  for no brand, model or serial; brands only appear because the model volunteers them in tags and descriptions.

### Results: `qwen2.5vl-jetson:3b`, 19 photos per row

Times in seconds, medians over the 19 warm requests. Raw data:
[`bench/results/sweep-2026-10-18/`](../bench/results/sweep-2026-10-18) (`sweep.jsonl` per request,
`tegrastats.log`, `sweep.log`, and `scored.md` with every answer).

| Model tag | Sent at | Warm median (p90) | Image + prompt tokens | Prompt eval | Output tokens / time | Unreported (vision) | Cold (load) | Loaded size | Peak RAM MB | Category | Name | Brand |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|
| `qwen2.5vl-jetson:3b` (501760) | 1024 px | 13.93 (14.51) | 745 | 2.33 | 66 / 4.13 | 7.05 | 32.5 (18.2) | 4.19 GB | 5918 | 16/19 | 17/19 | 7/10 |
| `qwen2.5vl-jetson:3b-400k` | 1024 px | 11.38 (11.66) | 630 | 2.05 | 66 / 4.04 | 4.82 | 27.6 (15.5) | 3.99 GB | 5612 | 18/19 | 17/19 | 6/10 |
| `qwen2.5vl-jetson:3b-300k` | 1024 px | 8.96 (9.51) | 496 | 1.33 | 66 / 4.09 | 3.20 | 24.7 (14.7) | 3.86 GB | 5637 | 16/19 | 17/19 | 6/10 |
| `qwen2.5vl-jetson:3b-200k` | 1024 px | 7.58 (7.93) | 376 | 1.04 | 66 / 4.08 | 2.00 | 21.2 (13.1) | 3.84 GB | 5443 | 15/19 | 15/19 | 6/10 |
| `qwen2.5vl-jetson:3b` | 672 px | 9.89 (12.16) | 568 | 1.67 | 65 / 3.99 | 3.97 | 26.8 (13.2) | 4.19 GB | 5902 | 16/19 | 16/19 | 6/10 |
| `qwen2.5vl-jetson:3b` | **560 px** | **8.32 (9.46)** | 436 | 1.27 | 65 / 4.03 | 2.49 | 27.9 (13.6) | 4.19 GB | 5822 | 16/19 | **18/19** | 7/10 |
| `qwen2.5vl-jetson:3b` | 448 px | 7.17 (7.57) | 328 | 1.00 | 65 / 4.02 | 1.57 | 28.9 (14.0) | 4.19 GB | 5815 | 17/19 | 15/19 | 6/10 |

Ollama's `load_duration` on a warm request was 0.34–0.39 s in every row. "Loaded size" is `size_vram` from `/api/ps`
after the cold request (100 % GPU in every case).

By photo set (category / name): original 9 and brand 10.

| Config | Original 9: category | name | Brand 10: category | name |
|---|---:|---:|---:|---:|
| 501760 @ 1024 px | 7 | 8 | 9 | 9 |
| 400k @ 1024 px | 8 | 8 | 10 | 9 |
| 300k @ 1024 px | 7 | 8 | 9 | 9 |
| 200k @ 1024 px | 6 | 7 | 9 | 8 |
| 501760 @ 672 px | 7 | 8 | 9 | 8 |
| 501760 @ 560 px | 8 | 9 | 8 | 9 |
| 501760 @ 448 px | 7 | 7 | 10 | 8 |

Observations:

- **Where the time goes.** Output is ~65 tokens at ~16 tok/s (≈ 4.0 s) in every row, so all of the saving comes from
  the image: the vision encoder (7.05 → 1.57 s) and the prompt evaluation of the image tokens (2.33 → 1.00 s).
- **Ceiling vs resize.** A lower `max_pixels` and a smaller upload end up in the same place: 200k @ 1024 px (7.6 s)
  ≈ 448 px (7.2 s), 300k (9.0 s) ≈ 560 px (8.3 s). The ceiling caps area, so wide photos keep more pixels than a
  long-edge resize gives them; resizing in the client also cuts the upload.
- **RAM and cold start.** Lower ceilings shrink the vision buffer reserved at load (4.19 → 3.84 GB) and the load time
  (18.2 → 13.1 s). A smaller upload does not change either: the reservation follows the model's ceiling.
- **Category errors are mostly the category list, not the size.** In almost every row the sneaker is put in
  Clothing, the coverless paperback in Documents or General, and the Osram lamp box in Electronics. Those three
  account for most category misses at every size.
- **Brand names** are found at every size when they are large (Sony, Fluke, Casio, Panadol, OSRAM, Raspberry Pi,
  Ibuprofen AL). Missed at every size: Makita (small logo on a held drill), Tefal (glossy logo), Panasonic (the label
  is not read at all; the VCR is called a "power supply", "radio receiver" or "audio equipment").

### Brand, model and serial results

Per photo, for the 1024 px baseline and the recommended 560 px (✓ exact, ~ partial, ✗ missed, – not in the photo).
Full answers for every configuration: [`scored.md`](../bench/results/sweep-2026-10-18/scored.md).

| Photo | Truth (brand / model / serial) | 1024 px brand · model · serial | 560 px brand · model · serial | 448 px brand · model · serial |
|---|---|---|---|---|
| sony_cd_player_label | Sony / CDP-CE525 / 5104890 | ✓ · ✗ · ✗ | ✓ · ✗ · ✗ | ✓ · ✓ · ✗ |
| panasonic_vcr_label | Panasonic / NV-G12EO / E7KL06311 | ✗ · ✗ · ✗ | ✗ · ✗ · ✗ | ✗ · ✗ · ✗ |
| makita_drill_dhp453 | Makita / DHP453 / – | ✗ · ✗ · – | ✗ · ✗ · – | ✗ · ✗ · – |
| ibuprofen_al400_pack | Ibuprofen AL / AL 400 / – | ✓ · ✓ · – | ✓ · ✓ · – | ~ · ✗ · – |
| raspberry_pi4 | Raspberry Pi / Raspberry Pi 4 Model B / 2ABCB-RPI4B | ✓ · ~ · ✗ | ✓ · ~ · ✗ | ✓ · ~ · ✗ |
| fluke45_multimeter | Fluke / 45 (any "45" counts, so this one is easy) / – | ✓ · ✓ · – | ✓ · ✓ · – | ✓ · ✓ · – |
| casio_fx991ex | Casio / fx-991EX / – | ✓ · ~ (read "fx-891ex") · – | ✓ · ✗ · – | ✓ · ✗ · – |
| tefal_kettle | Tefal / Express / – | ✗ · ✗ · – | ✗ · ✗ · – | ✗ · ✗ · – |
| panadol_500_pack | Panadol / 500 mg / 05 79 65 | ✓ · ✓ · ✗ | ✓ · ✓ · ✗ | ✓ · ✓ · ✗ |
| osram_lamp_box | OSRAM / – / – | ✓ · – · – | ✓ · – · – | ✓ · – · – |
| **Total** | 10 brands, 9 model strings, 4 serials | brand 7/10 · model 3 exact + 2 partial · serial 0/4 | brand 7/10 · model 3 exact + 1 partial · serial 0/4 | brand 6/10 · model 3 exact + 1 partial · serial 0/4 |

**No serial number was returned in any configuration.** That is expected with this prompt: it asks for none, and the
schema has no field for one. Reading serials would need a prompt and schema change (e.g. an optional `brand` /
`model_number` / `serial` field) and its own test; the photos and ground truth here can be reused for that.

### Qwen3-VL 2B does not run on this board with Ollama 0.15.6

`ollama pull qwen3-vl:2b` (Ollama 0.12.7 or newer is required; the board has 0.15.6) downloaded 1.9 GB. Two load
attempts with the service's prompt, schema and settings, and a 1024 px photo:

| Attempt | GPU layers | Compute graph | KV cache | Total | Outcome |
|---|---:|---:|---:|---:|---|
| Default (`num_ctx` from `OLLAMA_CONTEXT_LENGTH` = 8192) | fitting tried 29 → 0 | 4.2 GiB | 896 MiB | 7.0 GiB | "Load failed"; runner killed by the kernel OOM killer; HTTP 500 after 3 min 25 s |
| `options.num_ctx` = 2048 | 9 / 29 | 4.3 GiB (GPU) | 224 MiB | 6.7 GiB | runner killed by the OOM killer; HTTP 500 after 40 s |

Log excerpt: [`qwen3-vl-2b_load-attempts.log`](../bench/results/sweep-2026-10-18/qwen3-vl-2b_load-attempts.log).
Only the Ollama runner was killed both times; the service container and the rest of the board were unaffected.

The cause is the same as with stock `qwen2.5vl:3b`: at load Ollama reserves the vision graph for the largest image
the model accepts. For Qwen3-VL that limit is **hard-coded** in Ollama 0.15.6 (`longestEdge = 2 << 20`, 2 MP, in
`model/models/qwen3vl/imageprocessor.go`), not read from the GGUF, so the `max_pixels` patch used for
`qwen2.5vl-jetson:3b` cannot help, and Ollama has no flash attention on this GPU to shrink the buffer. Running it
would need a different runtime (for example llama.cpp's server with an image-token limit) or a newer Ollama that makes
the limit configurable; upgrading Ollama on the board was not attempted, because it serves the production model. The
model is kept installed for a future attempt, but must not be put in `ALLOWED_MODELS`.

### How to rerun

On the board as root, with nothing else using Ollama (every configuration unloads all models), from a checkout of this
repo and with the service image built (`deploy/jetson/build-image.sh` or `docker build -f Dockerfile.jetson`):

```bash
# 1. Extra max_pixels tags (needs the stock GGUF; same steps as "Building qwen2.5vl-jetson:3b" above).
ollama pull qwen2.5vl:3b
BLOB=$(python3 -c 'import json;m=json.load(open("/usr/share/ollama/.ollama/models/manifests/registry.ollama.ai/library/qwen2.5vl/3b"));print([l["digest"] for l in m["layers"] if l["mediaType"].endswith(".model")][0].replace(":","-"))')
mkdir -p /tmp/qwen-jetson && cp bench/qwen2.5vl-jetson/* /tmp/qwen-jetson/
docker run --rm --runtime runc --no-healthcheck -v /usr/share/ollama/.ollama/models/blobs:/blobs:ro \
  -v /tmp/qwen-jetson:/w -w /w --entrypoint bash apotheke-autofill:jetson -c \
  "python3.11 -m pip install -q --target /tmp/pp gguf numpy && for p in 200704 301056 401408; do \
   PYTHONPATH=/tmp/pp python3.11 gguf_set_maxpix.py /blobs/$BLOB qwen2.5vl-3b-maxpix\$p.gguf \$p; done"
cd /tmp/qwen-jetson && for s in 200k 300k 400k; do ollama create qwen2.5vl-jetson:3b-$s -f Modelfile.$s; done
rm /tmp/qwen-jetson/*.gguf && cd -          # Ollama keeps its own copies as blobs

# 2. Size sets (448/560/672/1024) and the cold-start photo. Needs Pillow; any machine will do.
python3 bench/make_sets.py bench/photos /tmp/sets && cp tests/fixtures/hardware/mug.jpg /tmp/sets/warmup.jpg

# 3. The sweep (about 25 minutes), then the tables.
bench/sweep.sh /tmp/sets /tmp/sweep-out apotheke-autofill:jetson \
  q25-501k@1024=qwen2.5vl-jetson:3b@1024 q25-400k@1024=qwen2.5vl-jetson:3b-400k@1024 \
  q25-300k@1024=qwen2.5vl-jetson:3b-300k@1024 q25-200k@1024=qwen2.5vl-jetson:3b-200k@1024 \
  q25-501k@672=qwen2.5vl-jetson:3b@672 q25-501k@560=qwen2.5vl-jetson:3b@560 q25-501k@448=qwen2.5vl-jetson:3b@448
python3 bench/score.py /tmp/sweep-out --per-photo > /tmp/sweep-out/scored.md

# 4. Put the service's model back (the last configuration already leaves it loaded with keep_alive -1).
ollama ps                                   # qwen2.5vl-jetson:3b, 100% GPU, Forever
```

The Modelfiles for the extra tags are in [`bench/qwen2.5vl-jetson/`](../bench/qwen2.5vl-jetson) (`Modelfile.200k`,
`Modelfile.300k`, `Modelfile.400k`); they are the production `Modelfile` with only the GGUF file name changed. The
committed `sweep.py` differs from the copy that produced these results only in formatting (ruff) and in reading the
photo with a `with` block.

## Setup

| | |
|---|---|
| Board | NVIDIA Jetson Xavier NX Developer Kit, hostname `<jetson-host>`, root filesystem on NVMe |
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
IMAGE=apotheke-autofill:jetson-<short sha>   # any built service image, as printed by deploy/jetson/build-image.sh
ollama pull qwen2.5vl:3b
# 1. Locate the stock GGUF (the "model" layer of the manifest).
BLOB=$(python3 -c 'import json;m=json.load(open("/usr/share/ollama/.ollama/models/manifests/registry.ollama.ai/library/qwen2.5vl/3b"));print([l["digest"] for l in m["layers"] if l["mediaType"].endswith(".model")][0].replace(":","-"))')
# 2. Copy it with the extra key. Needs Python 3.8+ with the gguf package; any Python works, here the service image's.
mkdir -p /tmp/qwen-jetson && cp bench/qwen2.5vl-jetson/* /tmp/qwen-jetson/
docker run --rm -v /usr/share/ollama/.ollama/models/blobs:/blobs:ro -v /tmp/qwen-jetson:/w -w /w \
  --entrypoint bash "$IMAGE" -c \
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

- Deployed on `<jetson-host>` on 2026-10-18 at `main` **7339211** (merge of PR #7; app code identical to e4e9033), image `apotheke-autofill:jetson-7339211`, model `qwen2.5vl-jetson:3b` with `OLLAMA_KEEP_ALIVE=-1`, `docker run --restart unless-stopped --env-file /opt/autofill/service.env`.
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
IMAGE=apotheke-autofill:jetson-2252ef5       # same tag convention as deploy/jetson/build-image.sh
docker build -f Dockerfile.jetson -t "$IMAGE" .

# Token header file (mode 600) from the service env file; the token is never printed.
sudo bench/make-auth-header.sh /opt/autofill/service.env /opt/autofill/auth.hdr

# Build qwen2.5vl-jetson:3b: see "Building qwen2.5vl-jetson:3b" above.

# Service passes (one per model), then put the service back on the chosen model.
export WARMUP_PHOTO=/path/to/a/photo-not-in-bench-photos.jpg
sudo -E bench/svcpass.sh moondream "$IMAGE" moondream
sudo -E bench/svcpass.sh qwen2.5vl-jetson:3b "$IMAGE" qwen
docker rm -f autofill && docker run -d --name autofill --network host --runtime nvidia \
  --restart unless-stopped --env-file /opt/autofill/service.env "$IMAGE"

sudo bench/verify.sh                       # health, info, cold recovery, two at once
sudo bench/bench100.sh qwen-2252ef5         # 100 sequential requests; prints mean/p50/p95/max, peak RAM, GPU temp

# Raw comparison straight against Ollama (stops/restarts Ollama; stop the service container first):
docker stop autofill && sudo bench/cmp2.sh "$IMAGE" moondream qwen2.5vl-jetson:3b; docker start autofill
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

## Brand / model / serial prompt variants (2026-10-19)

Feature 9 experiment: add optional `brand`, `model_number` and `serial_number` fields to the
service prompt and JSON schema, fill them only when the text is visibly readable (never guess),
and measure accuracy and latency against `bench/photos/ground_truth.json` plus
`bench/photos/brand_readability.json` (which sizes a person can read each value at).

### Recommendation

| Item | Value | Why |
|---|---|---|
| Prompt / schema variant | **`v3_lean`** | Fewest invented values (9 failures over both sizes vs 10 for `v1_empty` and **62** for `v2_omit`), same name/category as better variants, smallest latency tax (+0.8 s median). |
| Keep production model | `qwen2.5vl-jetson:3b` | Still best category score (16/19) and fastest practical VLM on this board among those tested. |
| `IMAGE_SIZES` | unchanged `448,560*,1024` | Brand fields add ~0.8–1.5 s; 560 px remains the default. Use 1024 px when serials matter (none readable at 560 on this set). |
| `ALLOWED_MODELS` | keep `qwen2.5vl-jetson:3b` | Do not allowlist MiniCPM / InternVL / SmolVLM / Gemma for production yet (see model shortlist below). |

**Timing note (GPU retime 2026-10-19 19:30–19:45 EEST):** after a board reboot at ~18:22 EEST, Ollama ran
CPU-only until DevOps restarted it at 19:28. A full warm retime of `base` and `v3_lean` at 560/1024 with CUDA
healthy (`GR3D` max 99%, `size_vram` equal to model size) reproduced the Feature 9 medians within noise:
base **8.58** / v3_lean **9.27** s at 560 (**+0.69** s) and base **13.89** / v3_lean **14.62** s at 1024
(**+0.73** s). Raw: `bench/results/brandserial-gpu-retimes-2026-10-19/`. The original Feature 9 accuracy
and the winner (`v3_lean`) are unchanged.

Service code still uses the base prompt today. Adopting `v3_lean` is a separate change to
`app/backends/ollama.py` (or a Feature flag). Exact winning text:
[`bench/results/brandserial-2026-10-19/winning_prompt.txt`](../bench/results/brandserial-2026-10-19/winning_prompt.txt) and
[`winning_schema.json`](../bench/results/brandserial-2026-10-19/winning_schema.json).

### Setup

Same board and Ollama 0.15.6 / `qwen2.5vl-jetson:3b` as the 2026-10-18 size sweep. Ran 2026-10-19
17:17–17:50 EEST via `bench/brand_sweep.sh` (variants `base`, `v1_empty`, `v2_omit`, `v3_lean` ×
sizes 560 and 1024). Scored with `bench/score_brand.py`.

Variants in short:

- **`base`:** current service prompt and schema (no brand fields).
- **`v1_empty`:** required `brand` / `model_number` / `serial_number` (`""` when unreadable).
- **`v2_omit`:** same fields optional (omit when unreadable). Invents heavily — do not use.
- **`v3_lean`:** like `v1_empty`, but tags max 3 and description under ~12 words.

### Scores (`qwen2.5vl-jetson:3b`)

| Config | Median s (Δ base) | Out tok (Δ) | Category (Δ) | Name (Δ) | Failures | Brand exact/partial of readable | Model no. | Serial |
|---|---:|---:|---:|---:|---:|---|---|---|
| base-560 | 8.52 | 65 | 16/19 | 18/19 | 0 | (in text 7/10) | (in text 3+1/9) | (in text 0/4) |
| v1_empty-560 | 9.99 (+1.47) | 85 (+20) | 18/19 (+2) | 18/19 | 5 | 7/1 of 8 | 2/0 of 4 | 1/0 of 1 |
| v2_omit-560 | 10.02 (+1.50) | 89 (+24) | 17/19 | 18/19 | **32** | 7/0 of 8 | 2/1 of 4 | 1/0 of 1 |
| **v3_lean-560** | **9.36 (+0.84)** | **79 (+14)** | **18/19 (+2)** | **18/19** | **5** | **7/1 of 8** | **1/1 of 4** | **1/0 of 1** |
| base-1024 | 13.95 | 66 | 16/19 | 17/19 | 0 | (in text 7/10) | (in text 3+2/9) | (in text 0/4) |
| v1_empty-1024 | 15.44 (+1.49) | 87 (+21) | 16/19 | 17/19 | 5 | 11/0 of 12 | 4/1 of 8 | 2/1 of 4 |
| v2_omit-1024 | 16.03 (+2.08) | 94 (+28) | 17/19 | 17/19 | **30** | 11/0 of 12 | 6/1 of 8 | 2/0 of 4 |
| **v3_lean-1024** | **14.78 (+0.83)** | **78 (+12)** | **16/19** | **17/19** | **4** | **11/0 of 12** | **3/2 of 8** | **2/1 of 4** |

Failures = invented + misread. "Readable" counts come from `brand_readability.json` (e.g. Panasonic
brand is not on the label; several models/serials only at 1024). Raw per-photo JSONL:
`bench/results/brandserial-2026-10-19/raw/`. Scripts: `bench/brand_variants.py`,
`bench/brand_sweep.py`, `bench/brand_sweep.sh`, `bench/score_brand.py`.

### Small-model shortlist on the same board (2026-10-19)

Pulled and exercised under the 30 Mbit/s cap. Production allowlist left as Qwen-only.

| Model | Tag used | Download | Result on Xavier NX 8 GB / Ollama 0.15.6 |
|---|---|---|---|
| Qwen2.5-VL 3B (baseline) | `qwen2.5vl-jetson:3b` | already installed | **Keep.** 560 px: 8.5 s median, category 16/19, name 18/19. |
| MiniCPM-V 4.0 | `openbmb/minicpm-v4:4b` (3.0 GB) | OK | Runs on GPU. 560 px: 10.4 s, category **9/19**, name 17/19. Better at some model numbers (5 exact at 560) but too many category misses for Apotheke. Keep on disk for OCR experiments; do not allowlist. |
| Gemma 3 4B | library `gemma3:4b` | **412** (needs newer Ollama) | HF `gemma3-hf:4b-q4_K_M` from `hf.co/ggml-org/gemma-3-4b-it-GGUF:Q4_K_M` loads for text; **vision `/api/chat` hangs >180 s**. Not usable on 0.15.6. |
| InternVL3 2B | `internvl3:2b-q4_K_M` (1.5 GB) | OK | **Not inherently CPU-only.** Early tests after the 18:22 reboot saw `GR3D=0%` / `size_vram=0` and ~110 s/photo. After CUDA restore: `size_vram` full, `GR3D` max 99%, hammer@560 **~19.5 s** (cold+vision). Still slower than Qwen; keep on disk, do not allowlist without a full accuracy sweep. |
| SmolVLM2 2.2B | `smolvlm2:2.2b-instruct-q4_K_M` (1.7 GB) | OK | Same boot-race story: ~83 s when Ollama lacked CUDA; **~17.1 s** hammer@560 on GPU after restore. Not a production candidate yet. |

Full modelbench raw (Qwen + MiniCPM at 560/1024; MiniCPM was timed while CUDA was still up): `bench/results/modelbench-2026-10-19/`.
Post-reboot CPU-only sample (historical): `bench/results/modelbench-2026-10-19/sample-cpu-legacy/`.
GPU smoke after CUDA restore: `bench/results/modelbench-gpu-smoke-2026-10-19/`.

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
