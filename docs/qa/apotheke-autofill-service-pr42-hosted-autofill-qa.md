# QA: apotheke-autofill-service #42, installation headers only via HostedAutofill (L-2190, service side)

- PR: https://github.com/thordin9/apotheke-autofill-service/pull/42 (Coder), `cursor/autofill-hosted-entrypoint` into `main`. Service half of the L-2190 pair; the edge half is apotheke #327 (`docs/qa/apotheke-pr327-hosted-autofill-qa.md` in that repo, reviewed at `ea5e61f5`).
- Reviewed head: `bf9266eb749ef17b955811df10a283a088a4da5b`. 0 behind main `49cfd5d4`, 2 ahead, mergeable `clean`.
- Commits: `a0477f4c` feat(worker): read installation headers only via HostedAutofill (63 chars) and `bf9266eb` ci(worker): allow the HostedAutofill export in the bundle check (63). Both are Conventional and at most 72.
- CI on the head: CI 44434680605 succeeded, 5/5 (lint-test, gitleaks, worker, deploy-scripts, image). The repo has no required checks and no CodeQL. Deploy Worker runs only on main.
- Gate: full, security-focused. Diff read, the Worker CI steps run locally, 11 service mutants (28 across the pair), local workerd probes of the public routes and of `HostedAutofill` through a real service binding from #327's code, docs and a secret scan.
- Reviewed 2026-11-19, 03:55 to 04:10 (UTC+2). Reviewer: Apotheke QA 2

## Checklist

- [x] Scope: 8 files, +127/−42: `worker/src/{index,installation}.ts`, `worker/test/{helpers,installation-budget.test}.ts`, `worker/scripts/local-info-check.sh`, `docs/api-v1.md`, `openapi.yaml` and CHANGELOG. `wrangler.toml`, `package*.json`, `.github/` and Python are unchanged. Dry runs (default and paid) list the same bindings as main.
- [x] Reachability: `HostedAutofill` is a named `WorkerEntrypoint`. Routes, custom domains and workers.dev only reach the `default` export, and `workers_dev` and `preview_urls` are false. Public probe: `/v1/info` without a token gave 401, `/HostedAutofill/v1/info` gave 404, and `?entrypoint=HostedAutofill` or a `cf-worker-entrypoint` header gave 401. The bundle check now allows exactly `Budget`, `HostedAutofill` and `default`. Any Worker in the same account can bind it (N-2293).
- [x] Public guard (`index.ts:299`): any of the three headers gives 400 before `authorize()`, so neither a token holder nor an anonymous caller can name an installation or limit, and nothing is counted. Probed with the token: canonical case, lower case, upper case, the soft header alone and an empty value all gave 400. Without a token, 400. `X_Apotheke_Installation` is ignored, since the service never reads it. The public path no longer reads installation headers at all, so the guard is defence in depth.
- [x] Hosted path: the installation is required (400 without it) and there's no bearer check. Rate-limit key `installation:<id>`, which can't collide with an IP or `unknown`. The id and limit validation from #41 is unchanged (`^\d{1,15}$`, at most 63 characters of `^[a-z0-9]+(-[a-z0-9]+)*$`, the same as the edge's `validDoName`). workerd probe through #327's code: `/v1/info` gave 200 with no token. tenant-b got hard 7, `autofill_limit` 429 on call 8, and per-installation `rate_limited` on call 11, with tenant-c unaffected.
- [x] Backward compatibility: no caller sends these headers (code search across all owner repos: only this repo; apotheke PHP sends only `Authorization`). Bearer requests without them behave exactly as before (test).
- [x] Run as CI does (Node 22.23.3, `npm ci`): `npm run typecheck` passes, vitest 92/92, both dry runs pass and `local-info-check.sh` passes. Python is untouched, so it wasn't rerun.
- [x] Mutants (service): 11 run, 10 killed. Killed: the guard removed, the guard checking only the installation header, the guard moved after `authorize`, the installation not required, `authorize` on the hosted path, an IP-only key, a key without the prefix, the default export as hosted, `HostedAutofill` as public, and the hosted path chosen by header. Surviving: M28 (`get` in place of `has`, equivalent, since the public path never reads the headers).
- [x] Docs: `api-v1.md` and `openapi.yaml` describe the entrypoint, the 400 and the rate-limit key. The CHANGELOG bullet is under [Unreleased] / Changed, but see N-2292. Released sections are byte-identical to main.
- [x] Secret scan: gitleaks 8.30.1 with the repo config on `main..head` found 0 leaks. Of the 127 added lines, 3 are secret-like hits, all `token: null` in tests.

## Prior findings

- **L-2190 closed by this pair, effective on this PR's deploy.** On the public routes the headers are now refused, and `HostedAutofill` takes them only from the edge, which sets them from its own state (#327).
- **L-2191 still open.** Hosted calls are counted before the model and the global gate (probe: 7 failed calls used up the month). Fix it before the edge binding is added.
- **N-2192 still open** (skipped). **N-2193 reduced:** only the edge can create installation DOs now; purge cleanup is still missing.

## New findings

- **N-2292:** The CHANGELOG bullet has no PR link, while the neighbouring bullets do. Add `([#42](https://github.com/thordin9/apotheke-autofill-service/pull/42))`.
- **N-2293** (shared with #327): The docs don't give the deploy order (deploy this PR before the edge's `AUTOFILL` binding is added) or the trust boundary (any Worker in the Cloudflare account can bind `HostedAutofill` without a token, so the account is the boundary).
- Edge-only Nits N-2290 and N-2291 are in the #327 doc.

0 High, 0 Medium, 0 Low and 2 Nit for this PR (N-2292, N-2293).

## Verdict

**PASS** at `bf9266eb749ef17b955811df10a283a088a4da5b`, 0 behind main `49cfd5d4`, CI 44434680605 green. Squash as "feat(worker): read installation headers only via HostedAutofill" (63 characters, 69 with " (#42)"). Commit this doc byte-exact as the last commit (`docs/qa/apotheke-autofill-service-pr42-hosted-autofill-qa.md`), then re-read the hash and CI on that head. Merge and deploy this PR first, since it only hardens the service and is safe alone. Then merge apotheke #327, then add the binding.
