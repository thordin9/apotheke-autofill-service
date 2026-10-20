"""The Jetson deployment is decommissioned: its workflows and docs must say so."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANCHOR = "deploy-jetson.md#decommissioned-2026-10-20"


def test_jetson_workflows_have_legacy_header():
    for name in ("jetson-hardware.yml", "jetson.yml"):
        head = (ROOT / ".github" / "workflows" / name).read_text().splitlines()[0]
        assert head.startswith("# LEGACY (2026-10-20)"), name


def test_jetson_hardware_stays_dispatch_only():
    text = (ROOT / ".github" / "workflows" / "jetson-hardware.yml").read_text()
    on = text.split("\non:", 1)[1].split("\n\n", 1)[0]
    assert "workflow_dispatch" in on
    for trigger in ("pull_request", "push", "schedule", "workflow_run"):
        assert trigger not in on


def test_jetson_docs_marked_legacy():
    deploy = (ROOT / "docs" / "deploy-jetson.md").read_text()
    assert "## Decommissioned 2026-10-20" in deploy
    assert "**Legacy, decommissioned on 2026-10-20.**" in deploy.split("## Contents", 1)[0]
    assert "> **Legacy:** removed from the board on 2026-10-20" in deploy
    assert ANCHOR in (ROOT / "docs" / "hardware-ci.md").read_text()
    assert ANCHOR in (ROOT / "README.md").read_text()
    assert "Jetson Ollama tuning" in (ROOT / "deploy" / "jetson" / "ollama-10-autofill.conf").read_text()
