"""Score bench/brand_sweep.py output; prints Markdown tables and writes per-photo scores.

  python3 bench/score_brand.py RESULTS_DIR [--per-photo]

RESULTS_DIR holds raw/<label>.jsonl, prompts/ and schemas/ from bench/brand_sweep.sh. Replies
are scored as returned (no service post-processing). Ground truth: bench/photos/ground_truth.json
(brand, model, serial) and bench/photos/brand_readability.json (at which sizes each value can be
read by a person; visible_brand for original-set photos).

Per photo, field (brand, model_number, serial_number) and size, the reply is one of:
  exact      readable value, reply equal after lower-casing and keeping only letters and digits
  partial    readable value, reply contains it or shares a run of at least half of it (min. 3)
  misread    readable value, reply filled but neither of the above        (failure)
  missed     readable value, reply empty
  abstained  no value, or not readable at this size, and reply empty      (correct)
  invented   no value, or not readable, and reply filled with something else (failure)
  unseen     value not readable at this size, but the reply matches it (reported separately:
             right, but not from text we could read, so it may be a guess)
For the base variant (no such fields) the cells say instead how often name + tags + description
mention the value (bench/score.py's exact/partial). Name and category use bench/score.py
(name_keywords, category / also_accept) and are compared with the base variant on the same size;
so are latency (median wall time) and output tokens.

Also writes RESULTS_DIR/scored_per_photo.jsonl (one line per warm request with the parsed reply
and every outcome) and, for the suggested winner, RESULTS_DIR/winning_prompt.txt and
winning_schema.json (copies of prompts/<variant>.txt and schemas/<variant>.json). Winner rule:
fewest failures (invented + misread), then most exact + partial, then no name/category loss
against base, then lowest median wall time. Check it by hand before using it.
"""

import glob
import json
import os
import shutil
import sys

from score import NAN, esc, load_json, load_lines, match, med, norm, parse, score_one

HERE = os.path.dirname(os.path.abspath(__file__))
FIELDS = (("brand", "brand"), ("model_number", "model"), ("serial_number", "serial"))
FAIL = ("invented", "misread")


def truth_for(photo, gt_key, gt, rd):
    g, r = gt.get(photo, {}), rd.get(photo, {})
    value = g.get(gt_key)
    if gt_key == "brand" and not value:
        value = r.get("visible_brand")
    return value, r.get(gt_key)


def outcome(value, sizes, size, reply):
    reply = reply if isinstance(reply, str) else ""
    filled = bool(norm(reply))
    readable = value is not None and (sizes is None or size in sizes)
    if readable:
        if not filled:
            return "missed"
        if norm(reply) == norm(value):
            return "exact"
        return "partial" if match(value, reply) != "miss" else "misread"
    if not filled:
        return "abstained"
    if value is not None and match(value, reply) != "miss":
        return "unseen"
    return "invented"


def score_rec(rec, gt, rd):
    o = parse(rec["content"])
    g = gt[rec["photo"]]
    base = score_one(g, o)
    size = int(rec["set"]) if str(rec["set"]).isdigit() else rec["set"]
    res = {"cat_ok": base["cat_ok"], "name_ok": base["name_ok"], "parsed": bool(o), "o": o}
    for field, gt_key in FIELDS:
        value, sizes = truth_for(rec["photo"], gt_key, gt, rd)
        if rec["variant"] == "base":
            # no such field: report whether name/tags/description mention it (bench/score.py)
            res[field] = "text-" + base[gt_key] if gt_key in base else "n/a"
        else:
            res[field] = outcome(value, sizes, size, o.get(field))
        res[field + "_truth"] = value
    return res


def load_runs(d):
    runs = {}
    for path in sorted(glob.glob(os.path.join(d, "raw", "*.jsonl"))):
        for line in load_lines(path):
            rec = json.loads(line)
            runs.setdefault(rec["label"], []).append(rec)
    return runs


def summarise(label, recs, gt, rd):
    warm = [r for r in recs if not r["cold"] and not r.get("error")]
    scored = [(r, score_rec(r, gt, rd)) for r in warm]
    s = {
        "label": label,
        "variant": recs[0]["variant"],
        "set": recs[0]["set"],
        "n": len(warm),
        "errors": len([r for r in recs if not r["cold"] and r.get("error")]),
        "unparsed": sum(not x["parsed"] for _, x in scored),
        "truncated": sum(r.get("done_reason") == "length" for r in warm),
        "cat": sum(x["cat_ok"] for _, x in scored),
        "name": sum(x["name_ok"] for _, x in scored),
        "wall": med([r["wall_s"] for r in warm]),
        "eval": med([r["eval_count"] for r in warm if r.get("eval_count") is not None]),
        "prompt": med([r["prompt_eval_count"] for r in warm if r.get("prompt_eval_count")]),
    }
    for field, _ in FIELDS:
        tally = {}
        for _, x in scored:
            tally[x[field]] = tally.get(x[field], 0) + 1
        s[field] = tally
    s["fail"] = sum(s[f].get(k, 0) for f, _ in FIELDS for k in FAIL)
    s["hits"] = sum(s[f].get(k, 0) for f, _ in FIELDS for k in ("exact", "partial"))
    return s, scored


def field_cell(t):
    if any(k.startswith("text-") or k == "n/a" for k in t):
        return (
            f"in text: {t.get('text-exact', 0)}/{t.get('text-partial', 0)} of "
            f"{sum(v for k, v in t.items() if k.startswith('text-'))}"
        )
    readable = sum(t.get(k, 0) for k in ("exact", "partial", "misread", "missed"))
    return (
        f"{t.get('exact', 0)}/{t.get('partial', 0)} of {readable}"
        f" · miss {t.get('missed', 0)} · misread {t.get('misread', 0)}"
        f" · inv {t.get('invented', 0)} · abst {t.get('abstained', 0)}"
        f" · unseen {t.get('unseen', 0)}"
    )


def pick_winner(summaries):
    by_set = {}
    for s in summaries:
        by_set.setdefault(s["set"], {})[s["variant"]] = s
    totals = {}
    for variants in by_set.values():
        base = variants.get("base")
        for v, s in variants.items():
            if v == "base":
                continue
            t = totals.setdefault(v, {"fail": 0, "hits": 0, "loss": 0, "wall": 0.0})
            t["fail"] += s["fail"] + s["unparsed"] + s["truncated"]
            t["hits"] += s["hits"]
            if base:
                t["loss"] += max(0, base["cat"] - s["cat"]) + max(0, base["name"] - s["name"])
            t["wall"] += s["wall"]
    if not totals:
        return None, totals
    best = min(
        totals,
        key=lambda v: (totals[v]["fail"], -totals[v]["hits"], totals[v]["loss"], totals[v]["wall"]),
    )
    return best, totals


def main():
    d = sys.argv[1]
    gt = load_json(os.path.join(HERE, "photos", "ground_truth.json"))["photos"]
    rd = load_json(os.path.join(HERE, "photos", "brand_readability.json"))["photos"]
    summaries, per = [], []
    for label, recs in load_runs(d).items():
        s, scored = summarise(label, recs, gt, rd)
        summaries.append(s)
        per += scored
    summaries.sort(key=lambda s: (int(s["set"]) if str(s["set"]).isdigit() else 0, s["variant"]))
    base = {s["set"]: s for s in summaries if s["variant"] == "base"}
    print(
        "| Config | Set | Variant | n | Median s (Δ base) | Output tok (Δ) | Prompt tok "
        "| Category (Δ) | Name (Δ) | Failures | Brand exact/partial | Model no. | Serial no. |"
    )
    print("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|")
    for s in summaries:
        b = base.get(s["set"], {})
        n_txt = str(s["n"])
        for key, word in (("errors", "err"), ("unparsed", "bad JSON"), ("truncated", "cut")):
            if s[key]:
                n_txt += f" (+{s[key]} {word})"
        cells = [
            s["label"],
            str(s["set"]),
            s["variant"],
            n_txt,
            f"{s['wall']:.2f} ({s['wall'] - b.get('wall', NAN):+.2f})",
            f"{s['eval']:.0f} ({s['eval'] - b.get('eval', NAN):+.0f})",
            f"{s['prompt']:.0f}",
            f"{s['cat']}/{s['n']} ({s['cat'] - b.get('cat', 0):+d})",
            f"{s['name']}/{s['n']} ({s['name'] - b.get('name', 0):+d})",
            str(s["fail"]),
        ]
        cells += [field_cell(s[f]) for f, _ in FIELDS]
        print("| " + " | ".join(cells) + " |")
    with open(os.path.join(d, "scored_per_photo.jsonl"), "w") as out:
        for r, x in per:
            keep = {k: r.get(k) for k in ("label", "variant", "set", "photo", "wall_s")}
            keep.update({k: r.get(k) for k in ("eval_count", "prompt_eval_count", "content")})
            out.write(json.dumps({**keep, **{k: v for k, v in x.items() if k != "o"}}) + "\n")
    best, totals = pick_winner(summaries)
    print()
    if best:
        print("Variant totals over all sizes (failures incl. bad JSON / cut, hits, name+category")
        print("loss against base, sum of median s):")
        for v, t in sorted(totals.items()):
            print(
                f"- {v}: failures {t['fail']}, hits {t['hits']}, loss {t['loss']}, "
                f"{t['wall']:.2f} s"
            )
        print(f"\nSuggested winner: **{best}** (check by hand).")
        for sub, name, ext in (
            ("prompts", "winning_prompt", "txt"),
            ("schemas", "winning_schema", "json"),
        ):
            src = os.path.join(d, sub, f"{best}.{ext}")
            if os.path.exists(src):
                shutil.copy(src, os.path.join(d, f"{name}.{ext}"))
    if "--per-photo" not in sys.argv:
        return
    print()
    print("| Config | Photo | s | Out tok | Category | Name | Brand | Model no. | Serial no. |")
    print("|---|---|---:|---:|---|---|---|---|---|")
    for r, x in per:
        o = x["o"]
        cells = [
            r["label"],
            r["photo"][:-4],
            f"{r['wall_s']:.1f}",
            str(r.get("eval_count")),
            f"{o.get('category')} {'✓' if x['cat_ok'] else '✗'}",
            f"{esc(o.get('name'))} {'✓' if x['name_ok'] else '✗'}",
        ]
        for f, _ in FIELDS:
            reply = o.get(f)
            cells.append(f"{esc(reply) if reply else '–'} ({x[f]})")
        print("| " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
