"""Workers AI bench client for apotheke-autofill Feature 10 (v1.4) comparison.

  cfrun.py smoke MODEL_KEY PHOTO [--fmt N]
  cfrun.py run MODEL_KEY PHOTO...

Credentials are read from files at runtime and never printed/stored. Every call is checked
against the Neuron ledger first (hard cap CAP for the UTC day).
"""
import base64, datetime as dt, io, json, os, re, sys, time

import jsonschema, requests
from PIL import Image

REPO = "/workspace/aas-main-cfbench"
BENCH = f"{REPO}/bench"
WIN = f"{BENCH}/results/brandserial-2026-10-19"
OUT = "/workspace/cf-bench/cf-workers-ai-2026-10-19"
LEDGER = "/workspace/cf-bench/neurons-ledger.json"
STOPFLAG = "/workspace/cf-bench/STOP_AUTH"
CAP = 6000.0
MAX_OUT = 1024
B64_CAP = 1_500_000

# neurons per token (CF pricing page, 2026-10-19); $0.011 per 1000 neurons
MODELS = {
    "gemma4": {"id": "@cf/google/gemma-4-26b-a4b-it", "in": 9091e-6, "out": 27273e-6, "cached": None, "api": "chat"},
    "gemma4nt": {"id": "@cf/google/gemma-4-26b-a4b-it", "in": 9091e-6, "out": 27273e-6, "cached": None, "api": "chat",
                 "extra": {"chat_template_kwargs": {"enable_thinking": False}}},
    "qwen38": {"id": "@cf/qwen/qwen3.8-27b", "in": 40909e-6, "out": 290909e-6, "cached": 4545e-6, "api": "chat",
               "extra": {"reasoning_effort": "low"}},
    "qwen38nt": {"id": "@cf/qwen/qwen3.8-27b", "in": 40909e-6, "out": 290909e-6, "cached": 4545e-6, "api": "chat",
                 "extra": {"chat_template_kwargs": {"enable_thinking": False}}},
    "mistral31": {"id": "@cf/mistralai/mistral-small-3.1-24b-instruct", "in": 31876e-6, "out": 50488e-6, "cached": None, "api": "guided"},
}
LABEL = {"gemma4": "cf-gemma4-26b-560", "gemma4nt": "cf-gemma4-26b-nothink-560", "qwen38": "cf-qwen3.8-27b-560", "mistral31": "cf-mistral-small-3.1-560", "qwen38nt": "cf-qwen3.8-27b-nothink-560"}

def secret(p):
    with open(p) as fh:
        return fh.read().strip()

TOKEN = None
ACCT = None

def redact(s):
    s = str(s)
    for v in (TOKEN, ACCT):
        if v:
            s = s.replace(v, "[REDACTED]")
    return re.sub(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(bearer\s+)?[^\s\"',}]+", r"\1[REDACTED]", s)

PROMPT = open(f"{WIN}/winning_prompt.txt").read().rstrip("\n")
SCHEMA = json.load(open(f"{WIN}/winning_schema.json"))

def utc_day():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

def load_ledger():
    if os.path.exists(LEDGER):
        L = json.load(open(LEDGER))
    else:
        L = {"note": "Neuron ledger for ITAdvisor's CF bench. neurons = usage tokens x CF per-model rates "
                     "(pricing page 2026-10-19). Day = UTC (resets 00:00 UTC = 03:00 Athens). Cap is mine; team cap 8000/day.",
             "cap_per_utc_day": CAP, "days": {}}
    L["days"].setdefault(utc_day(), {"total": 0.0, "per_model": {}, "calls": []})
    return L

def save_ledger(L):
    tmp = LEDGER + ".tmp"
    json.dump(L, open(tmp, "w"), indent=1)
    os.replace(tmp, LEDGER)

def neurons(m, usage):
    if not usage:
        return None
    if isinstance(usage.get("neurons"), (int, float)):
        return round(usage["neurons"], 3)  # CF-reported
    pt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    ct = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    cached = 0
    det = usage.get("prompt_tokens_details") or {}
    if isinstance(det, dict):
        cached = det.get("cached_tokens") or 0
    rate_c = m["cached"] if m["cached"] is not None else m["in"]
    return round((pt - cached) * m["in"] + cached * rate_c + ct * m["out"], 3)

def encode(path):
    im = Image.open(path).convert("RGB")
    im.thumbnail((560, 560), Image.LANCZOS)
    for q in (85, 75, 65, 50):
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=q)
        b = base64.b64encode(buf.getvalue()).decode()
        if len(b) < B64_CAP:
            return b, im.size, q
    raise RuntimeError("image too big")

def body_for(key, b64, history=None, fmt=0):
    m = MODELS[key]
    uri = "data:image/jpeg;base64," + b64
    if m["api"] == "chat" or fmt in (1, 3):
        content = [{"type": "image_url", "image_url": {"url": uri}}, {"type": "text", "text": PROMPT}]
        msgs = [{"role": "user", "content": content}] + (history or [])
        b = {"messages": msgs, "temperature": 0, "max_completion_tokens": MAX_OUT}
        if m["api"] == "chat" or fmt == 3:
            b["response_format"] = {"type": "json_schema", "json_schema": {"name": "item", "schema": SCHEMA, "strict": True}}
        else:
            b["guided_json"] = SCHEMA; b["max_tokens"] = MAX_OUT; del b["max_completion_tokens"]
        b.update(m.get("extra", {}))
        return b
    # mistral 'guided' API, fmt 0: messages with image_url parts + guided_json
    content = [{"type": "image_url", "image_url": {"url": uri}}, {"type": "text", "text": PROMPT}]
    if fmt == 2:  # separate `image` field (llama-3.2-vision style)
        msgs = [{"role": "user", "content": PROMPT}]
        b = {"messages": msgs + (history or []), "image": uri}
    else:
        b = {"messages": [{"role": "user", "content": content}] + (history or [])}
    b.update({"guided_json": SCHEMA, "temperature": 0, "max_tokens": MAX_OUT})
    return b

def extract(d):
    r = d.get("result") if isinstance(d, dict) else None
    if not isinstance(r, dict):
        return None, None, None, None
    usage = r.get("usage")
    if "choices" in r:
        ch = r["choices"][0]; msg = ch.get("message") or {}
        rc = msg.get("reasoning_content") or msg.get("reasoning")
        return msg.get("content"), usage, ch.get("finish_reason"), rc
    resp = r.get("response")
    if isinstance(resp, (dict, list)):
        resp = json.dumps(resp)
    return resp, usage, r.get("finish_reason"), None

def check_json(content):
    try:
        o = json.loads(content or "")
    except ValueError:
        return False, False, "not JSON"
    if not isinstance(o, dict):
        return False, False, "not an object"
    try:
        jsonschema.validate(o, SCHEMA); return True, True, None
    except jsonschema.ValidationError as e:
        return True, False, e.message[:200]

def call(key, body, photo, kind, est_prompt=1500):
    if os.path.exists(STOPFLAG):
        sys.exit("STOP: auth error flagged earlier; no more calls")
    m = MODELS[key]
    L = load_ledger(); day = L["days"][utc_day()]
    worst = est_prompt * m["in"] + MAX_OUT * m["out"]
    if day["total"] + worst >= CAP:
        print(f"BUDGET STOP: total {day['total']:.1f} + worst {worst:.1f} >= {CAP}", flush=True)
        return None
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCT}/ai/run/{m['id']}"
    t0 = time.time()
    try:
        r = requests.post(url, headers={"Authorization": "Bearer " + TOKEN}, json=body, timeout=180)
        status = r.status_code
        try:
            d = r.json()
        except ValueError:
            d = {"_text": r.text[:500]}
        exc = None
    except Exception as e:
        status, d, exc = None, {}, redact(repr(e))
    t1 = time.time()
    content, usage, finish, rc = extract(d)
    n = neurons(m, usage)
    errs = d.get("errors") if isinstance(d, dict) else None
    err = None
    if status != 200 or exc:
        err = redact(json.dumps({"status": status, "errors": errs, "exc": exc, "body": None if errs else d})[:800])
    if n is None and status == 200:
        n = round(worst, 3)  # usage missing: charge the worst case to stay safe
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "model": m["id"], "photo": photo,
           "kind": kind, "status": status, "usage": usage, "neurons": n or 0.0}
    day["calls"].append(rec)
    day["total"] = round(day["total"] + (n or 0.0), 3)
    day["per_model"][m["id"]] = round(day["per_model"].get(m["id"], 0.0) + (n or 0.0), 3)
    save_ledger(L)
    codes = [e.get("code") for e in (errs or []) if isinstance(e, dict)]
    if status in (401, 403) and 5035 not in codes:
        open(STOPFLAG, "w").write(redact(err or ""))
        print("AUTH ERROR, stopping all calls:", err, flush=True)
        sys.exit(3)
    return {"status": status, "wall_s": round(t1 - t0, 3), "t_start": t0, "t_end": t1, "content": content,
            "usage": usage, "neurons": n, "finish": finish, "reasoning_chars": len(rc) if rc else 0,
            "error": err, "codes": codes, "result_keys": sorted((d.get("result") or {}).keys()) if isinstance(d.get("result"), dict) else None}

REPAIR = ("Your previous reply was not a valid JSON object matching the required schema ({why}). "
          "Reply again with only the JSON object, no other text.")

def one(key, photo, fmt=0, kind="run"):
    b64, size, q = encode(f"{BENCH}/photos/{photo}")
    first = call(key, body_for(key, b64, fmt=fmt), photo, kind)
    if first is None:
        return None
    rec = {"label": LABEL[key], "model": MODELS[key]["id"], "set": "560", "variant": "v3_lean", "photo": photo,
           "cold": False, "img_px": list(size), "img_q": q, "img_b64_bytes": len(b64), "req_fmt": fmt}
    pj, sv, why = check_json(first["content"]) if first["status"] == 200 else (False, False, "http error")
    rec.update({"t_start": first["t_start"], "t_end": first["t_end"], "wall_s": first["wall_s"], "error": first["error"],
                "content_first": first["content"], "json_first": pj, "schema_first": sv, "why_first": why,
                "usage_first": first["usage"], "neurons_first": first["neurons"], "finish_first": first["finish"],
                "reasoning_chars_first": first["reasoning_chars"], "result_keys": first["result_keys"]})
    final, wall_total, n_total, usage2, repaired = first["content"], first["wall_s"], first["neurons"] or 0, None, False
    if first["status"] == 200 and not sv:
        hist = [{"role": "assistant", "content": first["content"] or ""}, {"role": "user", "content": REPAIR.format(why=why)}]
        second = call(key, body_for(key, b64, history=hist, fmt=fmt), photo, kind + "-repair")
        if second is not None:
            repaired = True
            final = second["content"]; wall_total += second["wall_s"]; n_total += second["neurons"] or 0
            usage2 = second["usage"]
            rec["repair_error"] = second["error"]
    pj2, sv2, why2 = check_json(final) if final is not None else (False, False, "none")
    u = first["usage"] or {}
    rec.update({"repaired": repaired, "content": final, "json_final": pj2, "schema_final": sv2, "why_final": why2,
                "usage_repair": usage2, "wall_total_s": round(wall_total, 3), "neurons_total": round(n_total, 3),
                "usd_total": round(n_total * 0.011 / 1000, 6),
                "eval_count": u.get("completion_tokens"), "prompt_eval_count": u.get("prompt_tokens"),
                "done_reason": "length" if first["finish"] == "length" else first["finish"]})
    return rec

def main():
    global TOKEN, ACCT
    TOKEN = secret("/workspace/cf-workers-ai-token"); ACCT = secret("/workspace/cf-account-id")
    mode, key = sys.argv[1], sys.argv[2]
    fmt = 0
    args = sys.argv[3:]
    if "--fmt" in args:
        i = args.index("--fmt"); fmt = int(args[i + 1]); del args[i:i + 2]
    sub = "smoke" if mode == "smoke" else "raw"
    os.makedirs(f"{OUT}/{sub}", exist_ok=True)
    path = f"{OUT}/{sub}/{LABEL[key]}.jsonl"
    for photo in args:
        rec = one(key, photo, fmt=fmt, kind=mode)
        if rec is None:
            print("stopped before", photo, flush=True); break
        with open(path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(LABEL[key], photo, rec["wall_s"], "json1", rec["json_first"], "schema1", rec["schema_first"],
              "rep", rec["repaired"], "final", rec["schema_final"], "N", rec["neurons_total"],
              "err", rec["error"], flush=True)
        if mode == "smoke":
            print("CONTENT:", rec["content_first"], "\nUSAGE:", rec["usage_first"], "finish", rec["finish_first"],
                  "reasoning_chars", rec["reasoning_chars_first"], "keys", rec["result_keys"], flush=True)

if __name__ == "__main__":
    main()
