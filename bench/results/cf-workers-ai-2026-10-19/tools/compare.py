"""Score CF raw results + Jetson v3_lean-560 baseline with bench/score_brand.py functions."""
import glob, json, os, statistics as st, sys
sys.path.insert(0, "/workspace/aas-main-cfbench/bench")
import jsonschema
from score_brand import FIELDS, FAIL, score_rec, field_cell
from score import load_json, parse

B = "/workspace/aas-main-cfbench/bench"
OUT = "/workspace/cf-bench/cf-workers-ai-2026-10-19"
gt = load_json(f"{B}/photos/ground_truth.json")["photos"]
rd = load_json(f"{B}/photos/brand_readability.json")["photos"]
SCHEMA = json.load(open(f"{B}/results/brandserial-2026-10-19/winning_schema.json"))

def valid(c):
    try:
        o = json.loads(c or "")
    except ValueError:
        return False, False
    if not isinstance(o, dict):
        return False, False
    try:
        jsonschema.validate(o, SCHEMA); return True, True
    except jsonschema.ValidationError:
        return True, False

def pct(v, p):
    v = sorted(v)
    if not v: return float("nan")
    k = (len(v) - 1) * p; f = int(k); c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)

def load(path):
    return [json.loads(l) for l in open(path)]

def summarise(label, recs, jetson=False, retime=None):
    recs = [r for r in recs if not r.get("cold")]
    ok = [r for r in recs if not r.get("error")]
    sc = [(r, score_rec(r, gt, rd)) for r in ok]
    s = {"label": label, "n": len(ok), "errors": len(recs) - len(ok)}
    s["cat"] = sum(x["cat_ok"] for _, x in sc); s["name"] = sum(x["name_ok"] for _, x in sc)
    for f, _ in FIELDS:
        t = {}
        for _, x in sc: t[x[f]] = t.get(x[f], 0) + 1
        s[f] = t
    s["fail"] = sum(s[f].get(k, 0) for f, _ in FIELDS for k in FAIL)
    s["hits"] = sum(s[f].get(k, 0) for f, _ in FIELDS for k in ("exact", "partial"))
    s["unseen"] = sum(s[f].get("unseen", 0) for f, _ in FIELDS)
    if jetson:
        v = [valid(r["content"]) for r in ok]
        s["json1"] = sum(a for a, _ in v); s["schema1"] = sum(b for _, b in v)
        s["json2"], s["schema2"] = s["json1"], s["schema1"]
        w = [r["wall_s"] for r in ok]
        s["p50"], s["p95"] = st.median(w), pct(w, .95)
        if retime:
            w2 = [r["wall_s"] for r in retime if not r.get("cold") and not r.get("error")]
            s["p50"], s["p95"] = st.median(w2), pct(w2, .95)
        s["neur"] = s["usd"] = 0.0
        s["out_tok"] = st.median([r["eval_count"] for r in ok]); s["in_tok"] = st.median([r["prompt_eval_count"] for r in ok])
        s["repairs"] = 0
    else:
        s["json1"] = sum(bool(r["json_first"]) for r in ok); s["schema1"] = sum(bool(r["schema_first"]) for r in ok)
        s["json2"] = sum(bool(r["json_final"]) for r in ok); s["schema2"] = sum(bool(r["schema_final"]) for r in ok)
        s["repairs"] = sum(bool(r["repaired"]) for r in ok)
        w = [r["wall_total_s"] for r in ok]
        s["p50"], s["p95"] = st.median(w), pct(w, .95)
        s["neur"] = st.mean([r["neurons_total"] for r in ok]); s["usd"] = s["neur"] * 0.011 / 1000
        s["neur_sum"] = sum(r["neurons_total"] for r in recs)
        s["out_tok"] = st.median([r["eval_count"] for r in ok]); s["in_tok"] = st.median([r["prompt_eval_count"] for r in ok])
        s["truncated"] = sum(r.get("done_reason") == "length" for r in ok)
    return s, sc

def main():
    rows, per = [], []
    jb = load(f"{B}/results/brandserial-2026-10-19/raw/v3_lean-560.jsonl")
    jr = load(f"{B}/results/brandserial-gpu-retimes-2026-10-19/raw/v3_lean-560.jsonl")
    s, sc = summarise("jetson qwen2.5vl-jetson:3b (v3_lean-560)", jb, jetson=True, retime=jr)
    rows.append(s); per += [("jetson", r, x) for r, x in sc]
    order = ["cf-gemma4-26b-560", "cf-gemma4-26b-nothink-560", "cf-qwen3.8-27b-560", "cf-qwen3.8-27b-nothink-560", "cf-mistral-small-3.1-560"]
    for lab in order:
        p = f"{OUT}/raw/{lab}.jsonl"
        if not os.path.exists(p): continue
        s, sc = summarise(lab, load(p)); rows.append(s); per += [(lab, r, x) for r, x in sc]
    print("| Config | n | Category | Name | Brand | Model no. | Serial no. | Failures (inv+misread) | Hits (exact+partial) | JSON valid 1st (schema) | After 1 repair (schema) | p50 s | p95 s | Out tok (med) | In tok (med) | Neurons/photo | $/photo |")
    print("|---|---:|---:|---:|---|---|---|---:|---:|---|---|---:|---:|---:|---:|---:|---:|")
    for s in rows:
        n = s["n"]
        print("| " + " | ".join([s["label"], f"{n}" + (f" (+{s['errors']} err)" if s["errors"] else ""),
              f"{s['cat']}/{n}", f"{s['name']}/{n}", field_cell(s["brand"]), field_cell(s["model_number"]),
              field_cell(s["serial_number"]), str(s["fail"]), str(s["hits"]),
              f"{s['json1']}/{n} ({s['schema1']})", f"{s['json2']}/{n} ({s['schema2']}), {s['repairs']} repaired",
              f"{s['p50']:.2f}", f"{s['p95']:.2f}", f"{s['out_tok']:.0f}", f"{s['in_tok']:.0f}",
              f"{s['neur']:.1f}", f"${s['usd']:.6f}"]) + " |")
    with open(f"{OUT}/scored_per_photo.jsonl", "w") as fh:
        for lab, r, x in per:
            keep = {k: r.get(k) for k in ("label", "variant", "set", "photo", "wall_s", "wall_total_s", "eval_count", "prompt_eval_count", "neurons_total", "repaired", "content")}
            fh.write(json.dumps({**keep, "config": lab, **{k: v for k, v in x.items() if k != "o"}}) + "\n")
    print()
    print("| Config | Photo | s | Category | Name | Brand | Model no. | Serial no. |")
    print("|---|---|---:|---|---|---|---|---|")
    for lab, r, x in per:
        o = x["o"]
        print("| " + " | ".join([lab, r["photo"][:-4], f"{r.get('wall_total_s', r['wall_s']):.1f}",
              f"{o.get('category')} {'✓' if x['cat_ok'] else '✗'}", f"{str(o.get('name'))[:30]} {'✓' if x['name_ok'] else '✗'}"] +
              [f"{str(o.get(f) or '–')[:24]} ({x[f]})" for f, _ in FIELDS]).replace("|", "\\|") .replace(" \\| ", " | ") + " |")

main()
