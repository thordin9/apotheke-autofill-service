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
    assert f"https://{DOMAIN}/healthz" in smoke["run"]
    assert "/v1/suggest" in smoke["run"]
    assert "401" in smoke["run"]
