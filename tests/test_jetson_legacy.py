"""The Jetson deployment is decommissioned: its workflows and docs must say so."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ANCHOR = "deploy-jetson.md#decommissioned-2026-10-20"


def test_jetson_workflows_have_legacy_header():
    for name in ("jetson-hardware.yml", "jetson.yml"):
        head = (ROOT / ".github" / "workflows" / name).read_text().splitlines()[0]
        assert head.startswith("# LEGACY (2026-10-20)"), name


@pytest.mark.parametrize("name", ["jetson-hardware.yml", "jetson.yml"])
def test_jetson_workflows_are_dispatch_only(name):
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())
    # PyYAML reads the bare key `on` as True.
    triggers = doc.get("on", doc.get(True))
    if isinstance(triggers, str):
        triggers = [triggers]
    assert list(triggers) == ["workflow_dispatch"], name


def test_jetson_docs_marked_legacy():
    deploy = (ROOT / "docs" / "deploy-jetson.md").read_text()
    assert "## Decommissioned 2026-10-20" in deploy
    assert "**Legacy, decommissioned on 2026-10-20.**" in deploy.split("## Contents", 1)[0]
    assert "> **Legacy:** removed from the board on 2026-10-20" in deploy
    assert ANCHOR in (ROOT / "docs" / "hardware-ci.md").read_text()
    readme = (ROOT / "README.md").read_text()
    assert ANCHOR in readme
    assert "Path-triggered runs" not in readme
    assert "github.event_name" not in (ROOT / ".github" / "workflows" / "jetson.yml").read_text()
    assert ANCHOR.split("/")[-1] in (ROOT / "docs" / "benchmarks.md").read_text()
    assert (
        "Jetson Ollama tuning"
        in (ROOT / "deploy" / "jetson" / "ollama-10-autofill.conf").read_text()
    )
