"""Unit tests for the pure helpers in scripts/hardware_smoke.py (no hardware)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("hardware_smoke", ROOT / "scripts/hardware_smoke.py")
hs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hs)


def test_suggestion_contract():
    good = {
        "name": {"value": "Mug", "confidence": 0.8},
        "category": {"value": "Kitchen"},
        "tags": [{"value": "red", "confidence": 1}],
        "description": {"value": "A red mug", "confidence": 0},
    }
    assert hs.check_suggestion(good) == []
    assert hs.check_suggestion({}) == []
    assert hs.check_suggestion([]) == ["body is not a JSON object"]
    assert hs.check_suggestion({"name": {"value": " "}})
    assert hs.check_suggestion({"name": {"value": "x", "confidence": 1.5}})
    assert hs.check_suggestion({"name": {"value": "x", "confidence": True}})
    assert hs.check_suggestion({"tags": "red"})
    assert hs.check_suggestion({"color": {"value": "red"}})


def test_info_contract():
    info = {"name": "m", "version": "1", "capabilities": ["name", "tags"], "ready": True}
    assert hs.check_info(info) == []
    assert hs.check_info({**info, "version": 1})
    assert hs.check_info({**info, "capabilities": ["colour"]})


def test_percentile_nearest_rank():
    vals = [float(i) for i in range(1, 21)]
    assert hs.percentile(vals, 50) == 10.0
    assert hs.percentile(vals, 95) == 19.0
    assert hs.percentile([3.0], 95) == 3.0
    assert hs.percentile([], 50) is None


def test_fixtures_are_jpegs():
    fixtures = sorted((ROOT / "tests/fixtures/hardware").glob("*.jpg"))
    assert len(fixtures) >= 3
    for f in fixtures:
        assert f.read_bytes()[:3] == b"\xff\xd8\xff", f


def test_tegrastats_summary(tmp_path):
    log = tmp_path / "t.log"
    log.write_text(
        "10-17-2026 23:00:00 RAM 3021/6854MB (lfb 4x4MB) SWAP 0/3427MB (cached 0MB) "
        "CPU [1%@1190] EMC_FREQ 0% GR3D_FREQ 0% AO@40C\n"
        "10-17-2026 23:00:01 RAM 5480/6854MB (lfb 2x4MB) SWAP 212/3427MB (cached 0MB) "
        "CPU [80%@1900] EMC_FREQ 40% GR3D_FREQ 99% AO@45C\n"
    )
    t = hs.tegrastats_summary(log)
    assert t["ram_start_mb"] == 3021 and t["ram_peak_mb"] == 5480 and t["ram_total_mb"] == 6854
    assert t["swap_peak_mb"] == 212 and t["gpu_peak_pct"] == 99 and t["samples"] == 2
    assert hs.tegrastats_summary(tmp_path / "missing.log") is None
