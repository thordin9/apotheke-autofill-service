#!/usr/bin/env python3
"""Hardware smoke test for a running autofill service (real backend).

Standard library only, Python >= 3.8: it runs on the Jetson host (L4T R35 ships
Ubuntu 20.04 / Python 3.8) without installing anything.

  wait    poll /healthz, then /v1/info until ready:true (model warm-up)
  run     exercise the service, write results JSON, exit 1 on a hard failure
  report  render results JSON (+ optional tegrastats log) as Markdown

Checks in `run`:
  * /healthz is open and ok
  * /v1/info and /v1/suggest answer 401 (unauthorized, WWW-Authenticate) without a token
  * /v1/info with the token matches the contract and reports ready
  * each fixture JPEG is posted with and without `context`; every 200 body must
    match the v1 Suggestion schema; every non-200 must be the JSON error envelope.
    503 backend_error / 504 backend_timeout are soft failures (the model gave an
    unusable answer or was slow): they count against --min-success, not as hard
    failures. Latency is recorded per request with p50/p95.
  * a concurrent pair (second request started --stagger-s after the first)
    must give exactly one 503 backend_unavailable with a Retry-After header.
    This needs the service started with OLLAMA_QUEUE_SIZE=0.

The token is read from the AUTOFILL_TOKEN environment variable and never printed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

FIELDS = ("name", "category", "tags", "description")
CAPABILITIES = set(FIELDS)
SOFT_CODES = {(503, "backend_error"), (504, "backend_timeout")}
KNOWN_ERROR_CODES = {
    "bad_request",
    "unauthorized",
    "payload_too_large",
    "unsupported_media_type",
    "backend_unavailable",
    "backend_error",
    "backend_timeout",
}
CONTEXT = {
    "categories": ["Kitchen", "Tools", "Drinks", "Books", "Electronics"],
    "tags": ["red", "yellow", "glass", "plastic", "metal", "paper"],
    "locale": "en",
}


# --------------------------------------------------------------------- HTTP


class Resp:
    def __init__(self, status: int, headers: dict[str, str], body: bytes, elapsed: float):
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body
        self.elapsed = elapsed

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None


def request(
    method: str,
    url: str,
    token: str | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: float = 60.0,
) -> Resp:
    req = urllib.request.Request(url, data=body, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if content_type:
        req.add_header("Content-Type", content_type)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return Resp(r.status, dict(r.headers), data, time.monotonic() - started)
    except urllib.error.HTTPError as e:
        data = e.read()
        return Resp(e.code, dict(e.headers or {}), data, time.monotonic() - started)
    except Exception as e:  # connection refused, client timeout, reset
        return Resp(0, {}, repr(e).encode(), time.monotonic() - started)


def multipart(image: bytes, filename: str, context: dict | None) -> tuple[bytes, str]:
    boundary = "----hwsmoke" + uuid.uuid4().hex
    parts = [
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode()
        + image
        + b"\r\n"
    ]
    if context is not None:
        parts.append(
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="context"\r\n\r\n'
                f"{json.dumps(context)}\r\n"
            ).encode()
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


# ----------------------------------------------------------------- contract


def check_scored(v: Any, where: str) -> list[str]:
    if not isinstance(v, dict):
        return [f"{where} is not an object"]
    errs = []
    extra = set(v) - {"value", "confidence"}
    if extra:
        errs.append(f"{where} has unexpected keys {sorted(extra)}")
    if not isinstance(v.get("value"), str) or not v["value"].strip():
        errs.append(f"{where}.value is not a non-empty string")
    if "confidence" in v:
        c = v["confidence"]
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 1:
            errs.append(f"{where}.confidence {c!r} is not a number in [0,1]")
    return errs


def check_suggestion(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return ["body is not a JSON object"]
    errs = []
    extra = set(body) - set(FIELDS)
    if extra:
        errs.append(f"unexpected top-level keys {sorted(extra)}")
    for key in ("name", "category", "description"):
        if key in body:
            errs += check_scored(body[key], key)
    if "tags" in body:
        if not isinstance(body["tags"], list):
            errs.append("tags is not a list")
        else:
            for i, t in enumerate(body["tags"]):
                errs += check_scored(t, f"tags[{i}]")
    return errs


def check_error(resp: Resp) -> tuple[str | None, list[str]]:
    body = resp.json()
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict) or not isinstance(err.get("code"), str):
        return None, [f"status {resp.status} body is not the error envelope"]
    errs = []
    if not isinstance(err.get("message"), str):
        errs.append("error.message missing")
    if err["code"] not in KNOWN_ERROR_CODES:
        errs.append("unknown error code {!r}".format(err["code"]))
    return err["code"], errs


def check_info(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return ["/v1/info body is not a JSON object"]
    errs = []
    if not isinstance(body.get("name"), str):
        errs.append("/v1/info name missing")
    if body.get("version") != "1":
        errs.append("/v1/info version is {!r}, not '1'".format(body.get("version")))
    caps = body.get("capabilities")
    if not isinstance(caps, list) or not set(caps) <= CAPABILITIES:
        errs.append(f"/v1/info capabilities {caps!r} not a subset of {sorted(CAPABILITIES)}")
    if "ready" in body and not isinstance(body["ready"], bool):
        errs.append("/v1/info ready is not a bool")
    return errs


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(p / 100.0 * len(s)) - 1)]


# --------------------------------------------------------------------- wait


def container_running(name: str) -> bool:
    try:
        out = (
            subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", name],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            .stdout.decode()
            .strip()
        )
    except (OSError, subprocess.TimeoutExpired):
        return True  # can't tell; let the timeout decide
    return out == "true"


def wait(args: argparse.Namespace) -> int:
    base = args.base_url.rstrip("/")
    token = os.environ.get("AUTOFILL_TOKEN", "").strip() or None
    started = time.monotonic()
    deadline = started + args.timeout
    healthy_at = None
    last = ""
    while time.monotonic() < deadline:
        if args.container and not container_running(args.container):
            print(f"container {args.container} is not running (crashed?)", file=sys.stderr)
            return 1
        if healthy_at is None:
            r = request("GET", base + "/healthz", timeout=5)
            if r.status == 200:
                healthy_at = time.monotonic() - started
                print(f"healthz ok after {healthy_at:.1f}s")
        if healthy_at is not None:
            r = request("GET", base + "/v1/info", token=token, timeout=30)
            body = r.json()
            if r.status == 200 and isinstance(body, dict) and body.get("ready", True) is True:
                ready_at = time.monotonic() - started
                print(f"ready after {ready_at:.1f}s: {json.dumps(body)}")
                if args.json_out:
                    Path(args.json_out).write_text(
                        json.dumps(
                            {
                                "healthz_s": round(healthy_at, 1),
                                "ready_s": round(ready_at, 1),
                                "info": body,
                            },
                            indent=2,
                        )
                    )
                return 0
            ready = body.get("ready") if isinstance(body, dict) else None
            now = f"info status {r.status} ready={ready!r}"
            if now != last:
                print(f"{time.monotonic() - started:5.0f}s {now}")
                last = now
        time.sleep(args.interval)
    print(
        "not ready after {:.0f}s ({})".format(args.timeout, last or "healthz never ok"),
        file=sys.stderr,
    )
    return 1


# ---------------------------------------------------------------------- run


def run(args: argparse.Namespace) -> int:
    base = args.base_url.rstrip("/")
    token = os.environ.get("AUTOFILL_TOKEN", "").strip()
    if not token:
        print("AUTOFILL_TOKEN is not set; the auth checks need it.", file=sys.stderr)
        return 2
    fixtures = sorted(Path(args.fixtures).glob("*.jpg"))
    if not fixtures:
        print(f"no *.jpg fixtures in {args.fixtures}", file=sys.stderr)
        return 2

    hard: list[str] = []
    out: dict[str, Any] = {"base_url": base, "started": time.time(), "checks": {}}

    def check(name: str, ok: bool, detail: str = "") -> None:
        out["checks"][name] = {"ok": ok, "detail": detail}
        print("{} {} {}".format("PASS" if ok else "FAIL", name, detail))
        if not ok:
            hard.append(f"{name}: {detail}")

    r = request("GET", base + "/healthz", timeout=10)
    body = r.json()
    check(
        "healthz",
        r.status == 200 and isinstance(body, dict) and body.get("status") == "ok",
        f"status {r.status}",
    )

    for method, path in (("GET", "/v1/info"), ("POST", "/v1/suggest")):
        data, ctype = (None, None)
        if method == "POST":
            data, ctype = multipart(fixtures[0].read_bytes(), fixtures[0].name, None)
        r = request(method, base + path, body=data, content_type=ctype, timeout=30)
        code, _ = check_error(r) if r.status != 200 else (None, [])
        ok = (
            r.status == 401
            and code == "unauthorized"
            and r.headers.get("www-authenticate", "").lower().startswith("bearer")
        )
        check(f"auth_required {method} {path}", ok, f"status {r.status} code {code}")
    r = request("GET", base + "/v1/info", token="wrong-" + uuid.uuid4().hex, timeout=30)
    check("auth_wrong_token", r.status == 401, f"status {r.status}")

    r = request("GET", base + "/v1/info", token=token, timeout=60)
    info = r.json()
    errs = check_info(info) if r.status == 200 else [f"status {r.status}"]
    out["info"] = info if isinstance(info, dict) else None
    check("info_contract", not errs, "; ".join(errs))
    if isinstance(info, dict):
        check("info_ready", info.get("ready", True) is True, "ready={!r}".format(info.get("ready")))
        if args.expect_backend:
            check(
                "info_backend",
                info.get("backend") == args.expect_backend,
                "backend={!r}".format(info.get("backend")),
            )

    # Sequential suggestions.
    results: list[dict[str, Any]] = []
    for rnd in range(args.rounds):
        for fx in fixtures:
            img = fx.read_bytes()
            for with_ctx in (False, True):
                data, ctype = multipart(img, fx.name, CONTEXT if with_ctx else None)
                r = request(
                    "POST",
                    base + "/v1/suggest",
                    token=token,
                    body=data,
                    content_type=ctype,
                    timeout=args.timeout,
                )
                item: dict[str, Any] = {
                    "round": rnd + 1,
                    "fixture": fx.name,
                    "context": with_ctx,
                    "status": r.status,
                    "latency_s": round(r.elapsed, 3),
                }
                if r.status == 200:
                    body = r.json()
                    errs = check_suggestion(body)
                    item["body"] = body
                    item["fields"] = sorted(body) if isinstance(body, dict) else []
                    item["outcome"] = "ok" if not errs else "contract_violation"
                    if errs:
                        item["errors"] = errs
                    if (
                        with_ctx
                        and isinstance(body, dict)
                        and isinstance(body.get("category"), dict)
                    ):
                        item["category_from_context"] = (
                            body["category"].get("value") in CONTEXT["categories"]
                        )
                elif r.status == 0:
                    item["outcome"] = "no_response"
                    item["errors"] = [r.body.decode(errors="replace")[:300]]
                else:
                    code, errs = check_error(r)
                    item["code"] = code
                    soft = (r.status, code) in SOFT_CODES and not errs
                    item["outcome"] = "soft_fail" if soft else "unexpected_error"
                    if errs:
                        item["errors"] = errs
                results.append(item)
                what = item.get("code") or item["outcome"]
                print(f"  {fx.name:<16} ctx={with_ctx!s:<5} -> {r.status} {what} {r.elapsed:.2f}s")
    out["suggest"] = results

    ok_n = sum(1 for x in results if x["outcome"] == "ok")
    soft_n = sum(1 for x in results if x["outcome"] == "soft_fail")
    bad = [x for x in results if x["outcome"] not in ("ok", "soft_fail")]
    lat_all = [x["latency_s"] for x in results if x["status"] != 0]
    lat_ok = [x["latency_s"] for x in results if x["outcome"] == "ok"]
    rate = ok_n / float(len(results))
    out["stats"] = {
        "requests": len(results),
        "ok": ok_n,
        "soft_fail": soft_n,
        "hard_fail": len(bad),
        "success_rate": round(rate, 3),
        "p50_s": percentile(lat_all, 50),
        "p95_s": percentile(lat_all, 95),
        "max_s": max(lat_all) if lat_all else None,
        "p50_ok_s": percentile(lat_ok, 50),
        "p95_ok_s": percentile(lat_ok, 95),
    }
    check(
        "suggest_contract",
        not bad,
        f"{len(bad)} response(s) broke the contract or had no response" if bad else "",
    )
    check(
        "suggest_success_rate",
        rate >= args.min_success,
        f"{ok_n}/{len(results)} ok (min {args.min_success:.0%})",
    )

    # Concurrent pair: one in flight, the other must be turned away busy.
    out["concurrency"] = concurrency(base, token, fixtures[0], args)
    c = out["concurrency"]
    check(
        "concurrent_pair_one_busy",
        c["busy"] == 1 and c["busy_with_retry_after"] == 1,
        f"statuses {c['statuses']}, busy={c['busy']}, "
        f"with Retry-After={c['busy_with_retry_after']}",
    )

    out["finished"] = time.time()
    out["hard_failures"] = hard
    Path(args.json_out).write_text(json.dumps(out, indent=2, ensure_ascii=False))
    st = out["stats"]
    print(
        f"\n{ok_n}/{len(results)} suggestions ok, p50 {st['p50_s'] or 0:.2f}s, "
        f"p95 {st['p95_s'] or 0:.2f}s; {len(hard)} hard failure(s)"
    )
    return 1 if hard else 0


def concurrency(base: str, token: str, fixture: Path, args: argparse.Namespace) -> dict[str, Any]:
    data, ctype = multipart(fixture.read_bytes(), fixture.name, None)
    got: list[Resp | None] = [None] * args.concurrency

    def worker(i: int) -> None:
        time.sleep(i * args.stagger_s)  # make sure the first one is in flight
        got[i] = request(
            "POST",
            base + "/v1/suggest",
            token=token,
            body=data,
            content_type=ctype,
            timeout=args.timeout,
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(args.concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = []
    busy = busy_ra = 0
    for r in got:
        assert r is not None
        code = check_error(r)[0] if r.status not in (0, 200) else None
        ra = r.headers.get("retry-after")
        is_busy = r.status == 503 and code == "backend_unavailable"
        busy += is_busy
        busy_ra += bool(is_busy and ra and ra.strip().isdigit())
        rows.append(
            {"status": r.status, "code": code, "retry_after": ra, "latency_s": round(r.elapsed, 3)}
        )
    return {
        "requests": rows,
        "statuses": [x["status"] for x in rows],
        "busy": busy,
        "busy_with_retry_after": busy_ra,
    }


# ------------------------------------------------------------------- report

TEGRA_RAM = re.compile(r"RAM (\d+)/(\d+)MB")
TEGRA_SWAP = re.compile(r"SWAP (\d+)/(\d+)MB")
TEGRA_GPU = re.compile(r"GR3D_FREQ (\d+)%")


def tegrastats_summary(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    ram, swap, gpu = [], [], []
    total_ram = total_swap = None
    for line in path.read_text(errors="replace").splitlines():
        m = TEGRA_RAM.search(line)
        if m:
            ram.append(int(m.group(1)))
            total_ram = int(m.group(2))
        m = TEGRA_SWAP.search(line)
        if m:
            swap.append(int(m.group(1)))
            total_swap = int(m.group(2))
        m = TEGRA_GPU.search(line)
        if m:
            gpu.append(int(m.group(1)))
    if not ram:
        return None
    return {
        "samples": len(ram),
        "ram_start_mb": ram[0],
        "ram_peak_mb": max(ram),
        "ram_total_mb": total_ram,
        "swap_peak_mb": max(swap) if swap else None,
        "swap_total_mb": total_swap,
        "gpu_peak_pct": max(gpu) if gpu else None,
    }


def fmt_s(v: float | None) -> str:
    return "–" if v is None else f"{v:.2f} s"


def report(args: argparse.Namespace) -> int:
    lines = ["## Jetson hardware smoke", ""]
    for note in args.note or []:
        lines.append("> " + note)
    if args.note:
        lines.append("")
    path = Path(args.results)
    res = json.loads(path.read_text()) if path.is_file() else None
    if res is None:
        lines.append("No smoke results (the run was skipped or stopped before the smoke step).")
    else:
        hard = res.get("hard_failures") or []
        lines.append("**Result: {}**".format("PASS" if not hard else f"FAIL ({len(hard)})"))
        lines.append("")
        info = res.get("info") or {}
        if info:
            lines.append(
                "Backend `{}`, name `{}`, ready `{}`.".format(
                    info.get("backend"), info.get("name"), info.get("ready", True)
                )
            )
            lines.append("")
        st = res.get("stats") or {}
        if st:
            lines += [
                "| Requests | OK | Soft fail | Hard fail | p50 | p95 | max | p50 (ok) | p95 (ok) |",
                "|---|---|---|---|---|---|---|---|---|",
                "| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                    st["requests"],
                    st["ok"],
                    st["soft_fail"],
                    st["hard_fail"],
                    fmt_s(st["p50_s"]),
                    fmt_s(st["p95_s"]),
                    fmt_s(st["max_s"]),
                    fmt_s(st["p50_ok_s"]),
                    fmt_s(st["p95_ok_s"]),
                ),
                "",
            ]
        lines += ["| Check | Result | Detail |", "|---|---|---|"]
        for name, c in (res.get("checks") or {}).items():
            lines.append(
                "| {} | {} | {} |".format(
                    name, "✅" if c["ok"] else "❌", c.get("detail", "").replace("|", "\\|")
                )
            )
        lines.append("")
        rows = res.get("suggest") or []
        if rows:
            lines += [
                "<details><summary>Per-request results</summary>",
                "",
                "| # | Fixture | Context | Status | Latency | Outcome | Fields / name |",
                "|---|---|---|---|---|---|---|",
            ]
            for i, x in enumerate(rows, 1):
                body = x.get("body") if isinstance(x.get("body"), dict) else {}
                name = (body.get("name") or {}).get("value", "") if body else ""
                cat = (body.get("category") or {}).get("value", "") if body else ""
                desc = ", ".join(x.get("fields", []))
                if name or cat:
                    desc += f" — {name} / {cat}"
                lines.append(
                    "| {} | {} | {} | {} | {} | {} | {} |".format(
                        i,
                        x["fixture"],
                        "yes" if x["context"] else "no",
                        x["status"],
                        fmt_s(x["latency_s"]),
                        x.get("code") or x["outcome"],
                        desc.replace("|", "\\|")[:120],
                    )
                )
            lines += ["", "</details>", ""]
    if args.wait_results and Path(args.wait_results).is_file():
        w = json.loads(Path(args.wait_results).read_text())
        lines += [
            "Warm-up: `/healthz` after {} s, `ready:true` after {} s.".format(
                w.get("healthz_s"), w.get("ready_s")
            ),
            "",
        ]
    if args.tegrastats:
        t = tegrastats_summary(Path(args.tegrastats))
        if t:
            lines += [
                f"### Memory (tegrastats, whole board, {t['samples']} samples)",
                "",
                "| RAM at start | RAM peak | RAM total | Swap peak | GPU peak |",
                "|---|---|---|---|---|",
                "| {} MB | {} MB | {} MB | {} MB | {}% |".format(
                    t["ram_start_mb"],
                    t["ram_peak_mb"],
                    t["ram_total_mb"],
                    t["swap_peak_mb"],
                    t["gpu_peak_pct"],
                ),
                "",
            ]
        else:
            lines += ["tegrastats: no samples recorded.", ""]
    text = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "a", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    r = sub.add_parser("run")
    r.add_argument("--base-url", default="http://127.0.0.1:18080")
    r.add_argument(
        "--fixtures",
        default=str(Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "hardware"),
    )
    r.add_argument("--rounds", type=int, default=2, help="passes over all fixtures")
    r.add_argument("--timeout", type=float, default=60.0, help="client timeout per request")
    r.add_argument(
        "--min-success",
        type=float,
        default=0.5,
        help="fraction of suggestions that must be 200 + valid",
    )
    r.add_argument("--expect-backend", default="ollama")
    r.add_argument("--concurrency", type=int, default=2)
    r.add_argument("--stagger-s", type=float, default=0.3)
    r.add_argument("--json-out", default="hardware-smoke.json")

    w = sub.add_parser("wait")
    w.add_argument("--base-url", default="http://127.0.0.1:18080")
    w.add_argument("--timeout", type=float, default=600.0)
    w.add_argument("--interval", type=float, default=3.0)
    w.add_argument("--container", help="fail fast if this docker container stops")
    w.add_argument("--json-out")

    rp = sub.add_parser("report")
    rp.add_argument("--results", default="hardware-smoke.json")
    rp.add_argument("--tegrastats")
    rp.add_argument("--wait-results")
    rp.add_argument("--note", action="append", help="line quoted at the top")
    rp.add_argument("--out", help="append here (e.g. $GITHUB_STEP_SUMMARY); default stdout")

    args = p.parse_args(argv)
    return {"wait": wait, "run": run, "report": report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
