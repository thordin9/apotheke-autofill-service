"""Size/model sweep straight against Ollama with the service's real prompt and schema.

Run inside the service image (it needs app.backends.ollama), one request at a time:

  docker run --rm --network host --runtime runc --entrypoint "" \
    -v $PWD/bench:/bench -v /path/to/photo-sets:/sets -v /path/to/out:/out \
    apotheke-autofill:jetson python3 /bench/sweep.py CONFIG [CONFIG ...]

CONFIG is  label=model_tag@set  (set = a directory under /sets, e.g. 1024 or 448).
For each config: every loaded model is unloaded (keep_alive 0) and the script waits until
/api/ps is empty, then one cold request is sent with $WARMUP (not a test photo), then every
*.jpg in /sets/<set> once, sorted. keep_alive is -1 like the service; options are the
service's (temperature 0, num_predict 256). Categories are Apotheke's default list.
Writes /out/sweep.jsonl (one line per request, appended) with Ollama's timing fields.
"""

import base64
import glob
import json
import os
import sys
import time

import httpx

from app.backends.ollama import build_prompt, build_schema

URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
WARMUP = os.environ.get("WARMUP", "/sets/warmup.jpg")
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
OUT = os.environ.get("OUT", "/out/sweep.jsonl")
c = httpx.Client(timeout=600)


def unload_all():
    for m in c.get(f"{URL}/api/ps").json().get("models", []):
        c.post(f"{URL}/api/generate", json={"model": m["name"], "keep_alive": 0})
    for _ in range(120):
        if not c.get(f"{URL}/api/ps").json().get("models"):
            break
        time.sleep(1)
    time.sleep(5)


def ask(model, path):
    with open(path, "rb") as fh:
        img = base64.b64encode(fh.read()).decode()
    body = {
        "model": model,
        "stream": False,
        "keep_alive": -1,
        "format": build_schema(CTX),
        "options": {"temperature": 0, "num_predict": 256},
        "messages": [{"role": "user", "content": build_prompt(CTX), "images": [img]}],
    }
    t0 = time.time()
    try:
        r = c.post(f"{URL}/api/chat", json=body)
        d = r.json()
        err = None if r.status_code == 200 else d
    except Exception as e:  # recorded, not fatal
        d, err = {}, repr(e)
    t1 = time.time()
    keys = (
        "total_duration",
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
        "done_reason",
    )
    return {
        "t_start": t0,
        "t_end": t1,
        "wall_s": round(t1 - t0, 3),
        "error": err,
        "content": (d.get("message") or {}).get("content"),
        **{k: d.get(k) for k in keys},
    }


with open(OUT, "a") as out:
    for cfg in sys.argv[1:]:
        label, rest = cfg.split("=", 1)
        model, pset = rest.split("@", 1)
        unload_all()
        rec = {
            "label": label,
            "model": model,
            "set": pset,
            "photo": "_cold_" + os.path.basename(WARMUP),
            "cold": True,
        }
        rec.update(ask(model, WARMUP))
        rec["ps"] = c.get(f"{URL}/api/ps").json().get("models")
        out.write(json.dumps(rec) + "\n")
        out.flush()
        print(label, "cold", rec["wall_s"], rec["error"], flush=True)
        for p in sorted(glob.glob(f"/sets/{pset}/*.jpg")):
            rec = {
                "label": label,
                "model": model,
                "set": pset,
                "photo": os.path.basename(p),
                "cold": False,
            }
            rec.update(ask(model, p))
            out.write(json.dumps(rec) + "\n")
            out.flush()
            print(label, rec["photo"], rec["wall_s"], rec["error"], flush=True)
