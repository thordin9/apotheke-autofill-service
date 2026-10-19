"""Worker deploy config and the main-only deploy workflow."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WRANGLER = ROOT / "worker" / "wrangler.toml"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-worker.yml"
DOMAIN = "apotheke-autofill-service.webapp.gr"
SHA = re.compile(r"^[0-9a-f]{40}$")


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
    routes = config["routes"]
    assert routes == [{"pattern": DOMAIN, "custom_domain": True}]
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
    assert "npx wrangler deploy" in deploy["run"]
    assert "secret put" not in text
    assert "set -x" not in text
    assert "echo" not in deploy["run"]
    smoke = next(step for step in job["steps"] if step.get("name") == "Smoke")
    assert smoke["env"]["WORKER_URL"] == "${{ vars.WORKER_URL }}"
    assert f"${{WORKER_URL:-https://{DOMAIN}}}" in smoke["run"]
    assert "$base/healthz" in smoke["run"]
    assert "/v1/suggest" in smoke["run"]
    suggest = smoke["run"].split("/v1/suggest", 1)[1]
    assert '[ "$code" = "401" ]' in suggest
    assert "200" not in suggest
    assert "last HTTP code:" in smoke["run"]


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
    assert set(deploy["env"]) == {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}


def test_deploy_checkout_drops_credentials_and_no_legacy_peer_deps():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
    npmrc = ROOT / "worker" / ".npmrc"
    assert not npmrc.exists() or "legacy-peer-deps" not in npmrc.read_text()
