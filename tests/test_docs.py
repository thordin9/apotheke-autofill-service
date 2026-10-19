"""The docs that QA checks name every new setting and contract field."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
NEW_ENV = [
    "ALLOWED_MODELS",
    "IMAGE_SIZES",
    "ECHO_MODEL",
    "ECHO_LOAD_MS",
    "ECHO_INSTALLED_MODELS",
    "ECHO_BRAND",
    "ECHO_MODEL_NUMBER",
    "ECHO_SERIAL_NUMBER",
]


@pytest.mark.parametrize("name", NEW_ENV)
def test_new_env_vars_documented(name):
    assert f"`{name}`" in (ROOT / "README.md").read_text()
    assert any(
        line.startswith(f"{name}=") for line in (ROOT / ".env.example").read_text().splitlines()
    )


@pytest.mark.parametrize(
    "field",
    [
        "models",
        "image_sizes",
        "model_used",
        "context.model",
        "brand",
        "model_number",
        "serial_number",
        "state",
        "size_vram",
    ],
)
def test_contract_fields_documented(field):
    assert f"`{field}`" in (ROOT / "docs" / "api-v1.md").read_text()
    assert field.rsplit(".", 1)[-1] + ":" in (ROOT / "openapi.yaml").read_text()


def test_env_example_has_no_inline_comments():
    # docker --env-file would keep them as part of the value.
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line and not line.startswith("#"):
            assert "#" not in line, line


def test_changelog_mentions_feature():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert "ALLOWED_MODELS" in text and "IMAGE_SIZES" in text and "v1.2" in text
    assert "v1.3" in text and "size_vram" in text and "brand" in text
