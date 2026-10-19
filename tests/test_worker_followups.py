"""Docs and config locks for the Worker follow-up after PR #25."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
QA = ROOT / "docs" / "qa" / "feature11-worker-live-qa.md"
QA_SHA = "3be8bc9b55402d564535aa179dcc5934daeed82b871587eca3adbece8a59b7b8"
QA_BYTES = 12395
QA_COMMIT = "ba1f8ff4397c568c5b9b5ffdc9f2e680664ca231"


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    rest = text[start + len(heading) :]
    nxt = rest.find("\n## [")
    return rest if nxt < 0 else rest[:nxt]


def test_live_qa_report_is_the_pinned_bytes():
    data = QA.read_bytes()
    assert len(data) == QA_BYTES
    assert hashlib.sha256(data).hexdigest() == QA_SHA


def test_gitleaks_qa_allowlist_is_commit_scoped():
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text())
    matched = [
        item
        for item in config["allowlists"]
        if any("stub-qa" in path for path in item.get("paths", []))
    ]
    assert len(matched) == 1
    entry = matched[0]
    assert entry["condition"] == "AND"
    assert entry["commits"] == [QA_COMMIT]
    assert entry["regexes"]


def test_openapi_allows_cloudflare_workers_backend():
    spec = yaml.safe_load((ROOT / "openapi.yaml").read_text())
    suggestion = spec["components"]["schemas"]["Suggestion"]["properties"]["backend_used"]
    assert suggestion["enum"] == ["ollama", "cloudflare", "cloudflare-workers"]
    info = spec["components"]["schemas"]["Info"]["properties"]["max_image_bytes"]["description"]
    assert "983040" in info
    assert "1048576" in info
    too_large = spec["components"]["responses"]["TooLarge"]["description"]
    assert "983040" in too_large


def test_changelog_moves_v3_lean_note_and_records_the_cap():
    text = (ROOT / "CHANGELOG.md").read_text()
    unreleased = _section(text, "## [Unreleased]")
    assert "1048576" in unreleased
    assert "983040" in unreleased
    assert "Retry-After: 30" in unreleased
    released = _section(text, "## [2.0.0]")
    assert "3145728" in released
    note = "The changelog's v3_lean cost is the measured +0.7 s."
    assert note not in _section(text, "## [1.4.0]")
    assert "v3_lean latency" not in _section(text, "## [1.4.0]")
    assert note in _section(text, "## [1.3.0]")


def test_jetson_known_issues_stay_documented():
    jetson = (ROOT / "docs" / "deploy-jetson.md").read_text()
    known = jetson.split("## Known issues", 1)[1]
    for label in ("**H2.", "**M5.", "**M6.", "**L6.", "**L7."):
        assert label in known
    backend = (ROOT / "docs" / "cloudflare-backend.md").read_text()
    for label in ("H2", "L6", "L7"):
        assert label in backend
        assert "deploy-jetson.md#known-issues" in backend
