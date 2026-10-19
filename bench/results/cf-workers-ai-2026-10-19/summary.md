# Cloudflare Workers AI vs Jetson: Feature 10 (v1.4) accuracy comparison, 2026-10-19

Run by ITAdvisor on the shared box, 21:33–21:52 EEST (18:33–18:52 UTC). Repo `thordin9/apotheke-autofill-service` main `5fabeaa`, read-only.
- **Prompt and schema:** v3_lean (`bench/results/brandserial-2026-10-19/winning_prompt.txt` and `winning_schema.json`), Apotheke default category list, locale en.
- **Photos:** all 19 bench photos (`bench/photos`). That is the whole bench set, so the "20–50" target wasn't reachable. Each photo was re-encoded from the 1024 px original to 560 px on the long side, Pillow LANCZOS, JPEG q85 (same as `make_sets.py`), with a base64 cap of 1.5 MB. Every photo came in at q85 and well under the cap.
- **Requests:** REST `POST /accounts/{id}/ai/run/{model}`, `messages` with an `image_url` data-URI part followed by the prompt text, `temperature 0`, max 1024 output tokens, and `response_format {type: json_schema, json_schema: {name, schema, strict: true}}`. Each call was made once. If a reply wasn't schema-valid, the script sent one repair turn (the bad reply plus "reply with only the JSON object").
- **Baseline:** the Jetson `qwen2.5vl-jetson:3b`, v3_lean-560, taken from `brandserial-2026-10-19/raw/v3_lean-560.jsonl` (accuracy) and `brandserial-gpu-retimes-2026-10-19/raw/v3_lean-560.jsonl` (latency, after the CUDA restore). Nothing was run on the Jetson.
- **Scoring:** `bench/score_brand.py` / `score.py` functions, unchanged (tools/compare.py imports them). Latency is the wall time from the box in Athens to CF, including the repair call if there was one. The Jetson latency is wall time on the LAN.
- **Neurons:** CF returns `usage.neurons` on every response, and it matched tokens × the pricing-page rate. $ = Neurons × $0.011/1000 (paid-plan rate; the free plan costs $0 up to 10k/day).

## Comparison (v3_lean, 560 px, 19 photos)

| Config | n | Category | Name | Brand | Model no. | Serial no. | Failures (inv+misread) | Hits (exact+partial) | JSON valid 1st (schema) | After 1 repair (schema) | p50 s | p95 s | Out tok (med) | In tok (med) | Neurons/photo | $/photo |
|---|---:|---:|---:|---|---|---|---:|---:|---|---|---:|---:|---:|---:|---:|---:|
| jetson qwen2.5vl-jetson:3b (v3_lean-560) | 19 | 18/19 | 18/19 | 7/1 of 8 · miss 0 · misread 0 · inv 1 · abst 5 · unseen 5 | 1/1 of 4 · miss 2 · misread 0 · inv 1 · abst 11 · unseen 3 | 1/0 of 1 · miss 0 · misread 0 · inv 3 · abst 15 · unseen 0 | 5 | 11 | 19/19 (19) | 19/19 (19), 0 repaired | 9.27 | 10.62 | 79 | 471 | 0.0 | $0.000000 |
| cf-gemma4-26b-560 | 19 | 19/19 | 19/19 | 7/0 of 8 · miss 0 · misread 1 · inv 2 · abst 4 · unseen 5 | 2/0 of 4 · miss 2 · misread 0 · inv 1 · abst 11 · unseen 3 | 1/0 of 1 · miss 0 · misread 0 · inv 0 · abst 17 · unseen 1 | 4 | 10 | 18/19 (18) | 19/19 (19), 1 repaired | 10.41 | 23.46 | 558 | 443 | 21.8 | $0.000240 |
| cf-gemma4-26b-nothink-560 | 19 | 19/19 | 18/19 | 7/1 of 8 · miss 0 · misread 0 · inv 2 · abst 4 · unseen 5 | 2/0 of 4 · miss 2 · misread 0 · inv 1 · abst 11 · unseen 3 | 1/0 of 1 · miss 0 · misread 0 · inv 0 · abst 17 · unseen 1 | 3 | 11 | 19/19 (19) | 19/19 (19), 0 repaired | 2.48 | 4.74 | 94 | 440 | 6.4 | $0.000070 |
| cf-qwen3.8-27b-560 | 19 (+1 err) | 18/19 | 18/19 | 8/0 of 8 · miss 0 · misread 0 · inv 2 · abst 4 · unseen 5 | 2/1 of 4 · miss 1 · misread 0 · inv 2 · abst 10 · unseen 3 | 1/0 of 1 · miss 0 · misread 0 · inv 1 · abst 17 · unseen 0 | 5 | 12 | 19/19 (19) | 19/19 (19), 0 repaired | 16.07 | 49.55 | 315 | 433 | 103.9 | $0.001143 |
| cf-qwen3.8-27b-nothink-560 | 19 | 18/19 | 18/19 | 8/0 of 8 · miss 0 · misread 0 · inv 2 · abst 4 · unseen 5 | 2/1 of 4 · miss 1 · misread 0 · inv 3 · abst 9 · unseen 3 | 1/0 of 1 · miss 0 · misread 0 · inv 2 · abst 16 · unseen 0 | 7 | 12 | 19/19 (19) | 19/19 (19), 0 repaired | 2.82 | 27.66 | 97 | 405 | 44.9 | $0.000494 |
| cf-mistral-small-3.1-560 | 19 | 19/19 | 18/19 | 7/0 of 8 · miss 1 · misread 0 · inv 2 · abst 5 · unseen 4 | 2/0 of 4 · miss 2 · misread 0 · inv 3 · abst 9 · unseen 3 | 1/0 of 1 · miss 0 · misread 0 · inv 2 · abst 16 · unseen 0 | 7 | 10 | 19/19 (19) | 19/19 (19), 0 repaired | 4.17 | 5.74 | 85 | 473 | 19.4 | $0.000213 |

Field cells use `score_brand.py`'s format: exact/partial of readable · missed · misread · invented · abstained · unseen. "Failures" = invented + misread. Full per-photo table in scored.md, per-photo scores in scored_per_photo.jsonl, raw replies in raw/.

## Smoke test (1 photo, sony_cd_player_label, smoke/)
| Model | 403/5035? | Image used? | Reply | Usage (in/out tok) | Neurons |
|---|---|---|---|---|---:|
| gemma-4-26b-a4b-it (default, thinking on) | none, 200 | yes | compact disc player, SONY, CDP-CE525, 5104890 (all exact) | 437 / 638 (≈1.5k chars reasoning) | 21.4 |
| gemma-4-26b-a4b-it, `chat_template_kwargs.enable_thinking=false` | none, 200 | yes | same, all exact | 434 / 112 | 7.0 |
| qwen3.8-27b, `reasoning_effort: low` | none, 200 | yes | compact disc player, SONY, CDP-CE525, 5104890 | 397 / 338 | 114.6 |
| qwen3.8-27b, `enable_thinking=false` | none, 200 | yes | same | 369 / 107 | 46.2 |
| mistral-small-3.1-24b-instruct, `guided_json` | none, 200 | **yes** (read model and serial exactly) | JSON wrapped in a ```json fence; **guided_json was ignored** and the repair turn returned a fence again | 431 / 97 (+ repair 559 / 97) | 41.4 |
| mistral-small-3.1-24b-instruct, `response_format` json_schema | none, 200 | yes | clean JSON, all exact | 431 / 101 | 18.8 |

So Mistral passes the image check. With `messages` input it has to be driven with `response_format`, because `guided_json` is silently ignored. The full run used `response_format` for Mistral.

## Rejected models
- `@cf/meta/llama-3.2-11b-vision-instruct` and `@cf/meta/llama-4-scout-17b-16e-instruct`: rejected without being called. The EU license / AUP grants no rights for multimodal models to EU-domiciled individuals or companies (PM decision 2026-10-19).
- `@cf/google/gemma-3-12b-it`: deprecated and removed on CF on 2026-05-30.
- None of the candidates failed the smoke test.

## Notes
- **Gemma 4 with thinking on** (the CF default) adds nothing on accuracy. It uses about 3.4× the Neurons and has 4× the p50 latency of thinking-off, and on 1/19 photos (panasonic_vcr_label) all 1024 output tokens went to reasoning, so the content came back empty (`finish_reason=length`). The repair turn fixed it. If Gemma is used, the service should send `chat_template_kwargs: {enable_thinking: false}`.
- **Qwen 3.8 (reasoning low)** hit a CF 408 timeout (code 3046, after 120 s) on paperback_book. A single retry succeeded, and the raw file keeps both lines. The ledger books the 408 at 0 Neurons because no usage came back; whether CF charged it is unverified. Qwen's p95 latency is high both with and without thinking.
- **CF latency is noisy.** Calls with similar token counts ranged from 2 s to 49 s, which looks like queueing on CF's side. Treat p95 as indicative only: this is one run from Athens at about 21:40 EEST.
- **Several "invented" outcomes may be real text that the ground truth doesn't list.** Four of the five models read GERLACH on the kitchen knife. Matsushita (the legal name on old Panasonic labels) appears on panasonic_vcr_label. "4 Model B" on the Raspberry Pi, where the sidecar says the model is only readable at 1024 px. Worth checking by eye before `ground_truth.json` / `brand_readability.json` change. The scores above use the files as they are.

## Neuron ledger (UTC day 2026-10-19)
See neurons-ledger.json (snapshot; the live file is /workspace/cf-bench/neurons-ledger.json). 104 calls, **3,979.6 Neurons total**, under my 6,000 cap:
- gemma-4-26b-a4b-it: 563.4 (smoke 28.4, run 507.5, repair 27.5)
- qwen3.8-27b: 2,987.5 (smoke 160.8, run 2,826.7)
- mistral-small-3.1-24b-instruct: 428.8 (smoke 37.5 + repair 22.7, run 368.6)

All 19 photos ran for every config, so nothing is left for tomorrow.

## Recommendation
Make the default **`@cf/google/gemma-4-26b-a4b-it` with `chat_template_kwargs: {enable_thinking: false}`**. Against the other CF configs and the Jetson baseline, it has:
- the fewest failures (3, against the Jetson's 5) and 19/19 categories
- 19/19 replies schema-valid on the first try
- p50 2.5 s and p95 4.7 s
- 6.4 Neurons (≈ $0.00007) per photo, the cheapest config: about 1,500 photos a day fit in the free 10k, or about 1,250 in the team's 8k

Qwen 3.8 gains nothing on accuracy, costs 7–16× the Neurons and has a bad latency tail. Mistral works only with `response_format` (its `guided_json` is ignored) and has more failures (7).
