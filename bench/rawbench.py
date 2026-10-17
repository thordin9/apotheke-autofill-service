"""Raw model capture straight against Ollama, using the service's own prompt and parser
(app.backends.ollama build_prompt/parse_suggestion, so run it inside the service image).

Env: OLLAMA_MODEL, OLLAMA_URL, NUM_PREDICT (default 256, 0 = no cap), PHOTO_GLOB (default
/photos/*.jpg), REQ_TIMEOUT (seconds, default 300), OUT_TAG (suffix for the output file).
One request at a time; per-photo errors are recorded. Writes /out/raw_<model>_cap<N><tag>.json.
"""

import base64
import glob
import json
import os
import time

import httpx

from app.backends.ollama import build_prompt, parse_suggestion

URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("OLLAMA_MODEL", "moondream")
CAP = int(os.environ.get("NUM_PREDICT", "256"))
GLOB = os.environ.get("PHOTO_GLOB", "/photos/*.jpg")
TMO = float(os.environ.get("REQ_TIMEOUT", "300"))
TAG = os.environ.get("OUT_TAG", "")
ctx = {
    "categories": [
        "Tools",
        "Electronics",
        "Clothing",
        "Books",
        "Kitchen",
        "Furniture",
        "Lighting",
        "Shoes",
        "Other",
    ],
    "locations": ["Garage", "Office", "Kitchen", "Bedroom", "Living room"],
}
out = []
for f in sorted(glob.glob(GLOB)):
    opts = {"temperature": 0}
    if CAP:
        opts["num_predict"] = CAP
    with open(f, "rb") as fh:
        img = fh.read()
    p = {
        "model": MODEL,
        "stream": False,
        "format": "json",
        "options": opts,
        "keep_alive": "30m",
        "messages": [
            {
                "role": "user",
                "content": build_prompt(ctx),
                "images": [base64.b64encode(img).decode()],
            }
        ],
    }
    t = time.time()
    d, err = {}, None
    try:
        resp = httpx.post(URL + "/api/chat", json=p, timeout=TMO)
        d = resp.json()
        if resp.status_code != 200:
            err = f"HTTP {resp.status_code}: {d.get('error')}"
    except Exception as e:
        err = repr(e)
    el = time.time() - t
    raw = (d.get("message") or {}).get("content", "")
    parsed = None
    if err is None:
        try:
            parsed = parse_suggestion(raw, ctx)
        except Exception as e:
            err = "parse: " + repr(e)
    r = {
        "photo": os.path.basename(f),
        "image_bytes": len(img),
        "secs": round(el, 2),
        "done_reason": d.get("done_reason"),
        "eval_count": d.get("eval_count"),
        "prompt_eval_count": d.get("prompt_eval_count"),
        "load_s": round((d.get("load_duration") or 0) / 1e9, 2),
        "prompt_eval_s": round((d.get("prompt_eval_duration") or 0) / 1e9, 2),
        "tok_per_s": round(d["eval_count"] / (d["eval_duration"] / 1e9), 1)
        if d.get("eval_duration")
        else None,
        "parsed": parsed,
        "error": err,
        "raw": raw,
    }
    out.append(r)
    print(json.dumps({k: v for k, v in r.items() if k != "raw"}, default=str), flush=True)
with open(f"/out/raw_{MODEL.replace(':', '_')}_cap{CAP}{TAG}.json", "w") as fh:
    json.dump(out, fh, indent=1, default=str)
