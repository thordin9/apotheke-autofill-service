# QA: service PR #28, Jetson deployment marked legacy

- PR: #28, draft, base `main` = `d0e959a9620b39d173eebfd1cc5b55c467248fc8` (merge of #26; current main, confirmed from
  the codeload tarball, and the tree is identical to QA's #26 copy)
- Head: `a1d9e968be7898b78d9a296a3d3746a81b509984` (2 commits: `34d5052` docs, `a1d9e96` test formatting)
- Date: 2026-10-20 (EEST). Docs, workflow comments and one test. No live calls, no board access.

## 1. Plan

1. Delta `main` → head: files, and confirm no code or `worker/**` change (so no Worker deploy on merge).
2. Workflows: LEGACY headers, Actions state of both Jetson workflows, triggers that would fire if they were
   re-enabled, other references to them, YAML validity (PyYAML and actionlint).
3. Docs: banners in `docs/deploy-jetson.md` and `docs/hardware-ci.md`, the bandwidth-limit note, the README note,
   the retitled Ollama conf, the CHANGELOG entry. Check the "Decommissioned 2026-10-20" section against QA's
   before/after snapshots of the board (10:15:56 and 10:18:16 EEST). Check links and anchors.
4. Tests: the 3 new tests, plus a mutation check. Full `pytest` (expect 563) and `deploy/jetson/tests/run-tests.sh`
   (expect 282). Run ruff.
5. Secrets and hosts: gitleaks on the added lines and the new test; grep for IPs, hostnames, emails, tokens and the
   production Worker hostname. CI on the head.

## 2. Results

### Delta

8 files, +87/−0: the two Jetson workflows (5-line comment header each), `CHANGELOG.md`, `README.md`,
`deploy/jetson/ollama-10-autofill.conf` (one comment line), `docs/deploy-jetson.md`, `docs/hardware-ci.md`, and
the new `tests/test_jetson_legacy.py`. No line is removed, and nothing changes under `app/`, `worker/`, `scripts/`
or any shell script. `deploy-worker.yml` only fires on `worker/**` and its own file, so **merging does not
deploy**. **PASS**

### Workflows

| Workflow | Header | Actions state | Triggers if re-enabled | Runner |
|---|---|---|---|---|
| `jetson-hardware.yml` "Jetson hardware" | LEGACY (2026-10-20) | `disabled_manually` (10:18:33 EEST) | `workflow_dispatch` only | `[self-hosted, jetson]` |
| `jetson.yml` "Jetson image" | LEGACY (2026-10-20) | `disabled_manually` (10:45:40 EEST) | `workflow_dispatch`; `pull_request` and `push` to `main` when `Dockerfile.jetson`, `requirements-jetson.txt` or the workflow change | `ubuntu-latest` (hosted), `contents: read`, path runs only echo a reason |

- `ci.yml` and `deploy-worker.yml` are still `active`. No workflow calls, `workflow_run`s or `needs` the Jetson ones.
- Other references: README:182-186 (jetson.yml triggers, present tense, under the new legacy note),
  `docs/hardware-ci.md` (under its banner), `docs/deploy-jetson.md:554` (legacy page), `Dockerfile.jetson:16`
  (comment), and `docs/benchmarks.md:476-478` (not marked, see N-2).
- YAML: all 4 workflows load with PyYAML. actionlint reports only the custom `jetson` runner label (expected; the
  same on `main`). **PASS**

### Docs against the board

Board facts (QA snapshots): the `autofill` container and 3 images (`apotheke-autofill:jetson-5fabeaa`,
`:jetson-517352f`, `dustynv/ollama:r35.4.1`) removed. `dl-cap.service`, `dl-cap-check.timer`/`.service`, `dl-cap.sh`,
`dl-cap-check.sh`, NM hook `90-dl-cap`, the `eth0` ingress qdisc with its IPv4/IPv6 police filters, and the
`/run` and `/run/lock` hook locks removed. `/opt/autofill` removed, with `service.env*` and `auth.hdr` shredded first.
Archive on the board (outside the repo): mode 600, 202 files, 4,635,061 bytes. Kept: Ollama with all 18 models
(identical name→ID list before and after), `qwen2.5vl-jetson:3b` `c917b94b8c33` loaded at 100 % GPU,
`10-autofill.conf`, `ollama-wait-cuda.conf`, `override.conf`, `wait-cuda.sh`, `ollama-gpu-check.service`/`.sh`, the
runner. 0 failed units. There is no `wg0` on the board (the tunnel ends on DevOps's computer).

| Claim in the PR | Matches the board? |
|---|---|
| deploy-jetson "Removed": container, `jetson-*` images and the `dustynv` base, `/opt/autofill/` (env files shredded first), all dl-cap units, scripts, NM hook, qdisc and filters, locks and markers under `/run` | Yes (auth header also shredded; see N-3) |
| "Benchmark data was archived on the board, outside the repo" | Yes (no path or host given, which is fine) |
| "Kept": Ollama and all models, `ollama-10-autofill.conf` (Jetson Ollama tuning), `ollama-wait-cuda.conf` + `wait-cuda.sh`, `ollama-gpu-check.service`, runner | Yes |
| Taken down after QA's end-to-end pass of v2.5.0 against the Worker | Yes (apotheke #28 QA live pass; v2.5.0 tag check PASS 10:15 EEST) |
| CHANGELOG: board no longer runs the service or the 30 Mbit/s cap; Ollama drop-ins stay | Yes |
| hardware-ci banner: "The service no longer runs on the board" | Yes |
| README note: "**nothing runs on the project's board any more**" | **No**: Ollama (Qwen loaded), `ollama-gpu-check` and the runner still run (L-1) |

The new section and the banners don't mention the tunnel or `wg0`, which is correct.
Links: 77 relative links and anchors in the 4 changed docs, 0 broken. `#decommissioned-2026-10-20` resolves from
README, hardware-ci and deploy-jetson. **PASS except L-1**

### Tests

- Local, Python 3.13: `pytest` **563 passed** (main: 560, so +3). `deploy/jetson/tests/run-tests.sh`
  **passed=282 failed=0**. `ruff check` and `ruff format --check` are clean.
- Mutation: QA added a blank line and then `pull_request_target:` inside the `on:` block of `jetson-hardware.yml`.
  The file is valid YAML with triggers `[workflow_dispatch, pull_request_target]`, but
  `test_jetson_hardware_stays_dispatch_only` **still passed**. It cuts the `on:` block at the first blank line
  (L-2).
- CI on the head: 5/5 (`lint-test`, `gitleaks`, `worker`, `deploy-scripts`, `image`; run 39388886789). **PASS
  except L-2**

### Secrets and hosts

gitleaks on the added lines and the new test found no leaks. The added lines contain no IPs, URLs, emails, tokens,
account ids or runner hostnames, and the production Worker hostname doesn't appear. **PASS**

## 3. Findings

| Id | Sev | Finding | Suggestion |
|---|---|---|---|
| L-1 | L | README:108 says "nothing runs on the project's board any more". Ollama (with Qwen loaded), `ollama-gpu-check` and the self-hosted runner still run there, as the PR's own deploy-jetson section says | "Decommissioned on 2026-10-20: the autofill service no longer runs on the project's board, and both Jetson workflows are disabled." |
| L-2 | L | `test_jetson_hardware_stays_dispatch_only` parses `on:` as text up to the first blank line, so a fork-reachable trigger after a blank line passes (shown by mutation) | `on = yaml.safe_load(text)[True]` (PyYAML is already a dev dependency and `test_worker_deploy.py` uses it) and `assert set(on) == {"workflow_dispatch"}` |
| L-3 | L (existing, PM decision) | The self-hosted runner stays registered to this public repo and online, but no enabled workflow uses it now. Its only safeguard is trigger discipline plus GitHub's fork-PR approval setting | Not caused by this PR. Either stop and deregister the runner (DevOps, on Stathis's go), or record in `docs/hardware-ci.md` that it is kept on purpose and that fork-PR workflows need approval |
| N-1 | nit | `jetson.yml` keeps its `pull_request`/`push` path triggers. If re-enabled they run on hosted `ubuntu-latest` with `contents: read` and only echo a reason, so this is harmless | Optional: make it `workflow_dispatch` only, like `jetson-hardware.yml` |
| N-2 | nit | `docs/benchmarks.md:476-478` ("Reproduce … or use the Jetson hardware workflow") isn't marked legacy | Optional one-line note that the workflow is disabled |
| N-3 | nit | The "Removed" list says "env files shredded first". The auth header file was shredded too | "env files and the auth header shredded first" |
| I-1 | info | actionlint flags only the custom `jetson` label (also on `main`). PyYAML loads every workflow | — |

No H or M findings.

## 4. Verdict

**PASS at `a1d9e968be7898b78d9a296a3d3746a81b509984`**, with no H or M findings. The PR is docs, comments and one test.
It removes no code and doesn't touch `worker/**`, so merging doesn't deploy. Both Jetson workflows are
`disabled_manually`, and the Decommissioned section matches the board. Tests are 563/282, CI is 5/5, and there are
no secrets or hosts. L-1 (a false README sentence) and L-2 (a test that can miss a trigger) are one-line fixes and
are recommended in this PR. If they are fixed, QA only re-checks the delta. L-3 is for the PM to decide and doesn't
block.
