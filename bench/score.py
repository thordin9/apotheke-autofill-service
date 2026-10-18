"""Score bench/sweep.py output against bench/photos/ground_truth.json; prints Markdown tables.

  python3 bench/score.py RESULTS_DIR [--per-photo]

RESULTS_DIR holds sweep.jsonl and tegrastats.log from bench/sweep.sh. The model's raw JSON reply
is scored as returned (no service post-processing).
- category: equal to the expected one (or one in also_accept)
- name: contains one of name_keywords (case-insensitive)
- brand / model / serial: lower-cased, reduced to letters and digits, and looked for in
  name + tags + description. "exact" = the whole string appears; "partial" = a common run of at
  least half its characters (min. 3) appears.
Warm figures are medians over the warm requests; "unreported" is total_duration minus load,
prompt eval and eval (mostly the vision encoder). Peak RAM is the highest tegrastats RAM value
between the config's first request start and last request end (cold request included).
"""

import json
import os
import re
import statistics as st
import sys
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
NAN = float("nan")


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def load_lines(path):
    with open(path) as fh:
        return fh.readlines()


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match(truth, text):
    t, x = norm(truth), norm(text)
    if t in x:
        return "exact"
    m = SequenceMatcher(None, t, x).find_longest_match(0, len(t), 0, len(x))
    return "partial" if m.size >= max(3, (len(t) + 1) // 2) else "miss"


def parse(content):
    try:
        out = json.loads(content or "")
        return out if isinstance(out, dict) else {}
    except ValueError:
        return {}


def med(values):
    return st.median(values) if values else NAN


def secs(recs, key):
    return [r[key] / 1e9 for r in recs if r.get(key) is not None]


def esc(s):
    return str(s).replace("|", "\\|")


def score_one(g, o):
    tags = " ".join(map(str, o.get("tags") or []))
    text = " ".join([str(o.get("name", "")), tags, str(o.get("description", ""))])
    res = {
        "cat_ok": o.get("category") == g["category"] or o.get("category") in g["also_accept"],
        "name_ok": any(k in str(o.get("name", "")).lower() for k in g["name_keywords"]),
    }
    for key in ("brand", "model", "serial"):
        if g.get(key):
            res[key] = match(g[key], text)
    return res


def main():
    d = sys.argv[1]
    gt = load_json(os.path.join(HERE, "photos", "ground_truth.json"))["photos"]
    recs = [json.loads(line) for line in load_lines(os.path.join(d, "sweep.jsonl"))]
    tegra = []
    tp = os.path.join(d, "tegrastats.log")
    if os.path.exists(tp):
        for line in load_lines(tp):
            m = re.match(r"(\d+\.\d+) .*?RAM (\d+)/(\d+)MB", line)
            if m:
                tegra.append((float(m[1]), int(m[2])))
    groups = {}
    for r in recs:
        groups.setdefault(r["label"], []).append(r)
    print(
        "| Config | Model | Set | n | Warm median s (p90) | Load s | Prompt tok / s "
        "| Output tok / s | Unreported s | Cold s (load s) | Peak RAM MB | Category | Name "
        "| Brand | Model no. (exact/partial) | Serial (exact/partial) |"
    )
    print("|---|---|---|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---|---|")
    per = []
    for label, rs in groups.items():
        cold = next((r for r in rs if r["cold"]), {})
        warm = [r for r in rs if not r["cold"] and not r.get("error")]
        errs = len([r for r in rs if not r["cold"] and r.get("error")])
        t0, t1 = min(r["t_start"] for r in rs), max(r["t_end"] for r in rs)
        peak = max([mb for t, mb in tegra if t0 <= t <= t1] or [0])
        walls = sorted(r["wall_s"] for r in warm)
        p90 = walls[round(0.9 * (len(walls) - 1))] if walls else NAN
        other = [
            (r["total_duration"] - r["load_duration"] - r["prompt_eval_duration"]) / 1e9
            - r["eval_duration"] / 1e9
            for r in warm
        ]
        tally = {"cat": 0, "name": 0, "brand": 0, "brand_n": 0}
        mod = {"exact": 0, "partial": 0, "miss": 0}
        ser = {"exact": 0, "partial": 0, "miss": 0}
        for r in warm:
            g, o = gt[r["photo"]], parse(r["content"])
            s = score_one(g, o)
            tally["cat"] += s["cat_ok"]
            tally["name"] += s["name_ok"]
            if g["set"] == "brand":
                tally["brand_n"] += 1
                tally["brand"] += s["brand"] == "exact"
            if "model" in s:
                mod[s["model"]] += 1
            if "serial" in s:
                ser[s["serial"]] += 1
            per.append({"label": label, "photo": r["photo"], "wall_s": r["wall_s"], "o": o, **s})
        n = len(warm)
        n_txt = f"{n} (+{errs} err)" if errs else str(n)
        cold_txt = f"{cold.get('wall_s', NAN):.1f} ({(cold.get('load_duration') or 0) / 1e9:.1f})"
        if cold.get("error"):
            cold_txt += " error"
        cells = [
            label,
            f"`{rs[0]['model']}`",
            rs[0]["set"],
            n_txt,
            f"{med(walls):.2f} ({p90:.2f})",
            f"{med(secs(warm, 'load_duration')):.2f}",
            f"{med([r['prompt_eval_count'] for r in warm]):.0f} / "
            f"{med(secs(warm, 'prompt_eval_duration')):.2f}",
            f"{med([r['eval_count'] for r in warm]):.0f} / {med(secs(warm, 'eval_duration')):.2f}",
            f"{med(other):.2f}",
            cold_txt,
            str(peak),
            f"{tally['cat']}/{n}",
            f"{tally['name']}/{n}",
            f"{tally['brand']}/{tally['brand_n']}",
            f"{mod['exact']}/{mod['partial']} of {sum(mod.values())}",
            f"{ser['exact']}/{ser['partial']} of {sum(ser.values())}",
        ]
        print("| " + " | ".join(cells) + " |")
    if "--per-photo" not in sys.argv:
        return
    print()
    print(
        "| Config | Photo | s | Category | Name | Brand | Model no. | Serial | Tags | Description |"
    )
    print("|---|---|---:|---|---|---|---|---|---|---|")
    for p in per:
        o = p["o"]
        cells = [
            p["label"],
            p["photo"][:-4],
            f"{p['wall_s']:.1f}",
            f"{o.get('category')} {'✓' if p['cat_ok'] else '✗'}",
            f"{esc(o.get('name'))} {'✓' if p['name_ok'] else '✗'}",
            p.get("brand", ""),
            p.get("model", ""),
            p.get("serial", ""),
            esc(", ".join(map(str, o.get("tags") or []))),
            esc(o.get("description")),
        ]
        print("| " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
