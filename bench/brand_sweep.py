"""Prompt-variant sweep (brand / model number / serial number) straight against Ollama.

Run inside the service image (it needs app.backends.ollama), via bench/brand_sweep.sh, one request
at a time, with nothing else using Ollama:

  python3 /bench/brand_sweep.py CONFIG [CONFIG ...]

CONFIG is  label=model_tag@set:variant  (set = a directory under $SETS_DIR, default /sets, e.g.
560 or 1024; variant = one of bench/brand_variants.py VARIANTS, e.g. base or v1_empty). When a
config's model differs from the one before (and for the first config), every loaded model is
unloaded (keep_alive 0), the script waits until /api/ps is empty, and one cold request is sent
with $WARMUP (not a test photo; default $SETS_DIR/warmup.jpg). Configs that reuse the loaded
model run warm straight away, so baseline and variants can be interleaved per size without
reloading. Every *.jpg in $SETS_DIR/<set> is sent once, sorted.
Options are the service's (temperature 0, num_predict 256, keep_alive -1); categories are
Apotheke's default list.

Output in $OUT_DIR (default /out):
  raw/<label>.jsonl     one line per request: photo, wall_s, error, the model's raw reply
                        (content), Ollama's timing fields; the cold line has "cold": true
  prompts/<variant>.txt the exact prompt sent
  schemas/<variant>.json the exact schema sent as `format`
"""

import base64
import glob
import json
import os
import sys
import time

import httpx
from brand_variants import build

from app.backends.ollama import build_prompt, build_schema

URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
WARMUP = os.environ.get("WARMUP", "")
OUT_DIR = os.environ.get("OUT_DIR", "/out")
SETS_DIR = os.environ.get("SETS_DIR", "/sets")
CATS = [
    "General",
    "Tools",
    "Electronics",
    "Kitchen",
    "Documents",
    "Hobby",
    "Clothing",
    "Shoes",
    "Books",
    "Lighting",
]
CTX = {"categories": CATS, "locale": "en"}
KEYS = (
    "total_duration",
    "load_duration",
    "prompt_eval_count",
    "prompt_eval_duration",
    "eval_count",
    "eval_duration",
    "done_reason",
)
c = httpx.Client(timeout=600)


def unload_all():
    for m in c.get(f"{URL}/api/ps").json().get("models", []):
        c.post(f"{URL}/api/generate", json={"model": m["name"], "keep_alive": 0})
    for _ in range(120):
        if not c.get(f"{URL}/api/ps").json().get("models"):
            break
        time.sleep(1)
    time.sleep(5)


def ask(model, path, prompt, schema):
    with open(path, "rb") as fh:
        img = base64.b64encode(fh.read()).decode()
    body = {
        "model": model,
        "stream": False,
        "keep_alive": -1,
        "format": schema,
        "options": {"temperature": 0, "num_predict": 256},
        "messages": [{"role": "user", "content": prompt, "images": [img]}],
    }
    t0 = time.time()
    try:
        r = c.post(f"{URL}/api/chat", json=body)
        d = r.json()
        err = None if r.status_code == 200 else d
    except Exception as e:  # recorded, not fatal
        d, err = {}, repr(e)
    t1 = time.time()
    return {
        "t_start": t0,
        "t_end": t1,
        "wall_s": round(t1 - t0, 3),
        "error": err,
        "content": (d.get("message") or {}).get("content"),
        **{k: d.get(k) for k in KEYS},
    }


def parse_config(cfg):
    label, rest = cfg.split("=", 1)
    model, rest = rest.rsplit("@", 1)
    pset, variant = rest.split(":", 1)
    return label, model, pset, variant


def save_variant(variant, prompt, schema):
    for sub in ("prompts", "schemas"):
        os.makedirs(os.path.join(OUT_DIR, sub), exist_ok=True)
    with open(os.path.join(OUT_DIR, "prompts", f"{variant}.txt"), "w") as fh:
        fh.write(prompt + "\n")
    with open(os.path.join(OUT_DIR, "schemas", f"{variant}.json"), "w") as fh:
        json.dump(schema, fh, indent=2)
        fh.write("\n")


def write(out, rec):
    out.write(json.dumps(rec) + "\n")
    out.flush()
    print(rec["label"], rec["photo"], rec["wall_s"], rec["error"], flush=True)


def main():
    configs = [parse_config(cfg) for cfg in sys.argv[1:]]
    base_prompt, base_schema = build_prompt(CTX), build_schema(CTX)
    os.makedirs(os.path.join(OUT_DIR, "raw"), exist_ok=True)
    warmup = WARMUP or os.path.join(SETS_DIR, "warmup.jpg")
    loaded = None
    for label, model, pset, variant in configs:
        prompt, schema = build(variant, base_prompt, base_schema)
        save_variant(variant, prompt, schema)
        photos = sorted(glob.glob(f"{SETS_DIR}/{pset}/*.jpg"))
        meta = {"label": label, "model": model, "set": pset, "variant": variant}
        with open(os.path.join(OUT_DIR, "raw", f"{label}.jsonl"), "a") as out:
            if model != loaded:
                unload_all()
                rec = {**meta, "photo": "_cold_" + os.path.basename(warmup), "cold": True}
                rec.update(ask(model, warmup, prompt, schema))
                rec["ps"] = c.get(f"{URL}/api/ps").json().get("models")
                write(out, rec)
                loaded = model
            for p in photos:
                rec = {**meta, "photo": os.path.basename(p), "cold": False}
                rec.update(ask(model, p, prompt, schema))
                write(out, rec)


if __name__ == "__main__":
    main()
