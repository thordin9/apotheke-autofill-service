"""Worker deploy config and the main-only deploy workflow."""

from __future__ import annotations

import re
import shlex
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
WRANGLER = ROOT / "worker" / "wrangler.toml"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-worker.yml"
DOMAIN = "autofill.services.getapotheke.eu"
LEGACY_DOMAIN = "apotheke-autofill-service.webapp.gr"
ROUTES = [
    {"pattern": DOMAIN, "custom_domain": True},
    {"pattern": LEGACY_DOMAIN, "custom_domain": True},
]
SHA = re.compile(r"^[0-9a-f]{40}$")


def _until_fi(text: str) -> str:
    kept = []
    for line in text.splitlines():
        if line.strip() == "fi":
            break
        kept.append(line)
    return "\n".join(kept)


def _walk(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def test_wrangler_toml_is_the_production_route():
    text = WRANGLER.read_text()
    config = tomllib.loads(text)
    assert config["workers_dev"] is False
    assert config["preview_urls"] is False
    assert "account_id" not in set(_walk(config))
    assert config["routes"] == ROUTES
    assert config["env"]["paid"]["routes"] == ROUTES
    migrations = config["migrations"]
    assert {"tag": "v1", "new_sqlite_classes": ["Budget"]} in migrations
    assert "workers.dev" not in text


def test_deploy_workflow_is_main_only_and_pinned():
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert "pull_request" not in triggers
    assert "pull_request_target" not in triggers
    push = triggers["push"]
    assert push["branches"] == ["main"]
    assert "worker/**" in push["paths"]
    assert ".github/workflows/deploy-worker.yml" in push["paths"]
    assert workflow["concurrency"]["group"] == "deploy-worker"
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["deploy"]
    assert job["environment"] == "production"
    assert job["runs-on"] == "ubuntu-latest"
    assert job["if"] == "github.ref == 'refs/heads/main'"
    uses = [step["uses"].split()[0] for step in job["steps"] if "uses" in step]
    assert uses
    for action in uses:
        name, pin = action.split("@", 1)
        assert "/" in name
        assert SHA.fullmatch(pin)
    assert any("# v" in line and "uses:" in line for line in text.splitlines())
    deploy = next(step for step in job["steps"] if step.get("name") == "Deploy")
    assert deploy["env"]["CLOUDFLARE_API_TOKEN"] == "${{ secrets.CLOUDFLARE_API_TOKEN }}"
    assert deploy["env"]["CLOUDFLARE_ACCOUNT_ID"] == "${{ secrets.CLOUDFLARE_ACCOUNT_ID }}"
    assert deploy["env"]["WORKER_DEPLOY_ENV"] == "${{ vars.WORKER_DEPLOY_ENV }}"
    assert "${{" not in deploy["run"]
    assert "npx wrangler deploy" in deploy["run"]
    assert "secret put" not in text
    assert "set -x" not in text
    assert "echo" not in deploy["run"]
    assert "printf" not in deploy["run"]
    smoke = next(step for step in job["steps"] if step.get("name") == "Smoke")
    assert smoke["env"] == {"WORKER_URL": "${{ vars.WORKER_URL }}"}
    assert "${{" not in smoke["run"]
    assert "Authorization" not in smoke["run"]
    assert f"${{WORKER_URL:-https://{DOMAIN}}}" in smoke["run"]
    assert "$base/healthz" in smoke["run"]
    assert "/v1/suggest" in smoke["run"]
    suggest = smoke["run"].split("/v1/suggest", 1)[1]
    assert '[ "$code" = "401" ]' in suggest
    assert "200" not in suggest
    assert "503" not in suggest
    assert "last HTTP code:" in smoke["run"]
    success = [
        line.strip()
        for line in suggest.splitlines()
        if line.strip().startswith("if ") and "code" in line
    ]
    assert success == ['if [ "$code" = "401" ]; then']


def test_wrangler_has_observability_and_no_kv():
    text = WRANGLER.read_text()
    config = tomllib.loads(text)
    assert config["observability"]["enabled"] is True
    assert config["observability"]["logs"]["enabled"] is True
    assert config["observability"]["traces"]["enabled"] is True
    assert "kv_namespaces" not in config
    assert "kv_namespaces" not in text
    assert config["vars"]["MAX_UPLOAD_BYTES"] == "1048576"


def test_deploy_workflow_installs_with_ci_and_checks_wrangler():
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)
    job = workflow["jobs"]["deploy"]
    assert "secrets" not in job
    assert "secrets.AUTOFILL_TOKEN" not in text
    assert "printf" not in text
    assert "npm install" not in text
    install = next(step for step in job["steps"] if step.get("name") == "Install")
    assert install["run"].strip() == "npm ci"
    test = next(step for step in job["steps"] if step.get("name") == "Test")
    assert test["run"].strip() == "npm test"
    version = next(step for step in job["steps"] if step.get("name") == "Check Wrangler version")
    assert "devDependencies.wrangler" in version["run"]
    assert "npx wrangler --version" in version["run"]
    deploy = next(step for step in job["steps"] if step.get("name") == "Deploy")
    # No if:, continue-on-error or other keys, so a failed deploy always fails the job.
    assert set(deploy) == {"name", "env", "run"}
    commands = [
        line.strip()
        for line in deploy["run"].splitlines()
        if line.strip().startswith("npx wrangler deploy")
    ]
    assert commands == [
        'npx wrangler deploy --dry-run --env "$WORKER_DEPLOY_ENV" | tee "$dry"',
        'npx wrangler deploy --dry-run | tee "$dry"',
        'npx wrangler deploy --env "$WORKER_DEPLOY_ENV" | tee "$log"',
        'npx wrangler deploy | tee "$log"',
    ]
    assert [part.split("|", 1)[0].strip() for part in commands] == [
        'npx wrangler deploy --dry-run --env "$WORKER_DEPLOY_ENV"',
        "npx wrangler deploy --dry-run",
        'npx wrangler deploy --env "$WORKER_DEPLOY_ENV"',
        "npx wrangler deploy",
    ]
    upload = 'npx wrangler deploy --env "$WORKER_DEPLOY_ENV" | tee "$log"'
    assert deploy["run"].index('tee "$dry"') < deploy["run"].index(upload)
    assert "||" not in deploy["run"]
    assert "continue-on-error" not in job
    assert set(deploy["env"]) == {
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
        "WORKER_DEPLOY_ENV",
    }
    assert "AUTOFILL_TOKEN" not in deploy["env"]
    assert "AUTOFILL_TOKEN" not in deploy["run"]
    names = [step.get("name") for step in job["steps"]]
    assert names.index("Test") < names.index("Deploy")


def test_deploy_runs_local_runtime_check_before_deploy():
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["deploy"]
    names = [step.get("name") for step in job["steps"]]
    check = job["steps"][names.index("Local runtime info check")]
    # No if:, continue-on-error or env, so the guard can't be skipped or softened.
    assert set(check) == {"name", "timeout-minutes", "run"}
    assert check["run"].strip() == "bash scripts/local-info-check.sh"
    assert names.index("Local runtime info check") < names.index("Deploy")


def test_local_info_check_reads_the_constant():
    script = (ROOT / "worker" / "scripts" / "local-info-check.sh").read_text()
    assert "ADVERTISED_MAX_IMAGE_BYTES" in script
    assert "524288" not in script
    assert "MULTIPART_HEADROOM" in script
    assert "wrangler.toml" in script


def test_deploy_checks_the_tier_binding_without_a_token():
    text = WORKFLOW.read_text()
    job = yaml.safe_load(text)["jobs"]["deploy"]
    deploy = next(step for step in job["steps"] if step.get("name") == "Deploy")
    run = deploy["run"]
    assert "dry=$(mktemp)" in run
    assert "log=$(mktemp)" in run
    assert 'tee "$dry"' in run
    assert 'tee "$log"' in run
    assert "set -euo pipefail" in run
    dry_pattern = 'grep -q -E \'TIER.*"paid"\' "$dry"'
    log_pattern = 'grep -q -E \'TIER.*"paid"\' "$log"'
    assert run.count(dry_pattern) == 2
    assert run.count(log_pattern) == 2
    upload = 'npx wrangler deploy --env "$WORKER_DEPLOY_ENV" | tee "$log"'
    assert run.index(dry_pattern) < run.index(upload)
    assert run.index(upload) < run.index(log_pattern)
    paid_parts = run.split('[ "${WORKER_DEPLOY_ENV:-}" = "paid" ]')[1:]
    assert len(paid_parts) == 2
    for paid in paid_parts:
        body = paid.split("elif", 1)[0]
        assert "if ! grep -q -E" in body
    free_parts = run.split('[ -z "${WORKER_DEPLOY_ENV:-}" ]')[1:]
    assert len(free_parts) == 2
    for free in free_parts:
        body = _until_fi(free)
        assert "if grep -q -E" in body
        assert "if ! grep -q -E" not in body
    assert "AUTOFILL_TOKEN" not in run
    assert "Authorization" not in run
    assert "echo" not in run
    assert "||" not in run


_PAID_TIER = 'env.TIER ("paid")   Environment Variable'
_FREE_TIER = 'env.TIER ("free")   Environment Variable'


@pytest.mark.parametrize(
    ("worker_deploy_env", "dry_line", "upload_line", "argv", "message"),
    [
        pytest.param(
            "paid",
            _FREE_TIER,
            _PAID_TIER,
            ["wrangler deploy --dry-run --env paid"],
            'dry-run output is missing a TIER binding of "paid"',
            id="paid-dry-run",
        ),
        pytest.param(
            "paid",
            _PAID_TIER,
            _FREE_TIER,
            ["wrangler deploy --dry-run --env paid", "wrangler deploy --env paid"],
            'deploy output is missing a TIER binding of "paid"',
            id="paid-upload",
        ),
        pytest.param(
            "",
            _PAID_TIER,
            _FREE_TIER,
            ["wrangler deploy --dry-run"],
            'free dry-run output shows a TIER binding of "paid"',
            id="free-dry-run",
        ),
        pytest.param(
            "",
            _FREE_TIER,
            _PAID_TIER,
            ["wrangler deploy --dry-run", "wrangler deploy"],
            'free deploy output shows a TIER binding of "paid"',
            id="free-upload",
        ),
    ],
)
def test_tier_mismatch_fails_the_step(
    tmp_path: Path,
    worker_deploy_env: str,
    dry_line: str,
    upload_line: str,
    argv: list[str],
    message: str,
):
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["deploy"]
    deploy = next(step for step in job["steps"] if step.get("name") == "Deploy")
    run = deploy["run"]
    upload = 'npx wrangler deploy --env "$WORKER_DEPLOY_ENV" | tee "$log"'
    pre, post = run.split(upload, 1)
    assert 'dry-run output is missing a TIER binding of "paid"' in pre
    assert 'free dry-run output shows a TIER binding of "paid"' in pre
    assert 'deploy output is missing a TIER binding of "paid"' in post
    assert 'free deploy output shows a TIER binding of "paid"' in post
    assert "dry-run output" not in post
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_log = tmp_path / "argv"
    log_q = shlex.quote(str(argv_log))
    npx = bin_dir / "npx"
    npx.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {log_q}\n"
        'case "$*" in\n'
        f"  *--dry-run*) printf '%s\\n' {shlex.quote(dry_line)} ;;\n"
        f"  *) printf '%s\\n' {shlex.quote(upload_line)} ;;\n"
        "esac\n"
        "exit 0\n"
    )
    npx.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", "-c", run],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "TMPDIR": str(tmp_path),
            "HOME": str(tmp_path),
            "WORKER_DEPLOY_ENV": worker_deploy_env,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert argv_log.read_text().splitlines() == argv
    assert result.stderr.strip() == message


def test_deploy_workflow_never_reads_autofill_token():
    text = WORKFLOW.read_text()
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert "AUTOFILL_TOKEN" not in code
    assert "AUTOFILL_TOKEN" in text
    job = yaml.safe_load(text)["jobs"]["deploy"]
    smoke = next(step for step in job["steps"] if step.get("name") == "Smoke")
    assert "Authorization" not in smoke["run"]
    assert "/v1/info" not in smoke["run"]
    assert "autofill-info.json" not in smoke["run"]
    assert "WORKER_DEPLOY_ENV" not in smoke.get("env", {})


def test_worker_deploy_env_is_a_simple_name():
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)
    job = workflow["jobs"]["deploy"]
    deploy = next(step for step in job["steps"] if step.get("name") == "Deploy")
    smoke = next(step for step in job["steps"] if step.get("name") == "Smoke")
    assert '[[ ! "${WORKER_DEPLOY_ENV}" =~ ^[a-z0-9][a-z0-9_-]*$ ]]' in deploy["run"]
    pattern = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
    assert pattern.fullmatch("paid")
    assert pattern.fullmatch("env_1-a")
    assert pattern.fullmatch("") is None
    assert pattern.fullmatch("Paid") is None
    assert pattern.fullmatch(" paid") is None
    assert pattern.fullmatch("paid ") is None
    assert pattern.fullmatch("../paid") is None
    assert pattern.fullmatch("paid;rm") is None
    assert pattern.fullmatch("--dry-run") is None
    assert pattern.fullmatch("-paid") is None
    assert "WORKER_DEPLOY_ENV must match ^[a-z0-9][a-z0-9_-]*$" in deploy["run"]
    assert "<<'EOF'" in deploy["run"]
    run = deploy["run"]
    assert run.index("if [ -n ") < run.index("npx wrangler deploy")
    assert run.index("=~ ^[a-z0-9][a-z0-9_-]*$") < run.index("npx wrangler deploy")
    dry_paid = 'npx wrangler deploy --dry-run --env "$WORKER_DEPLOY_ENV" | tee "$dry"'
    upload = 'npx wrangler deploy --env "$WORKER_DEPLOY_ENV" | tee "$log"'
    assert run.index(dry_paid) < run.index(upload)
    first_else = run.split("else", 1)[1]
    assert 'npx wrangler deploy --dry-run | tee "$dry"' in first_else
    assert run.index('npx wrangler deploy --dry-run | tee "$dry"') < run.index(
        'npx wrangler deploy | tee "$log"'
    )
    assert "Authorization" not in smoke["run"]


def test_deploy_trap_deletes_both_temp_logs(tmp_path: Path):
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"]["deploy"]
    deploy = next(step for step in job["steps"] if step.get("name") == "Deploy")
    run = deploy["run"]
    traps = [line.strip() for line in run.splitlines() if line.strip().startswith("trap ")]
    assert traps == ['trap \'rm -f "$dry" "$log"\' EXIT']
    assert run.index("dry=$(mktemp)") < run.index(traps[0])
    assert run.index("log=$(mktemp)") < run.index(traps[0])
    assert run.index(traps[0]) < run.index("npx wrangler deploy")
    dry = tmp_path / "dry"
    log = tmp_path / "log"
    dry.write_text("dry-run")
    log.write_text("upload")
    script = "\n".join(
        [
            "set -euo pipefail",
            f"dry={shlex.quote(str(dry))}",
            f"log={shlex.quote(str(log))}",
            traps[0],
            "exit 0",
        ]
    )
    subprocess.run(["bash", "-c", script], check=True)
    assert not dry.exists()
    assert not log.exists()


def test_deploy_checkout_drops_credentials_and_no_legacy_peer_deps():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
    npmrc = ROOT / "worker" / ".npmrc"
    assert not npmrc.exists() or "legacy-peer-deps" not in npmrc.read_text()
