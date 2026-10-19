"""Cloudflare credential files must be paths the container can read.

The service reads CF_API_TOKEN_FILE and CF_ACCOUNT_ID_FILE inside the
container. run-service.sh mounts the host directory /opt/autofill/data at
/var/lib/autofill and nothing above it, so a host path such as
/opt/autofill/cf-api-token is invisible in the container and the service
falls back to Ollama. This stays quiet until those settings exist (they
arrive with the Python Cloudflare backend).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "deploy" / "jetson" / "service.env.example"
DOC = ROOT / "docs" / "deploy-jetson.md"
RUN = ROOT / "deploy" / "jetson" / "run-service.sh"

TOKEN = "CF_API_TOKEN_FILE"
ACCOUNT = "CF_ACCOUNT_ID_FILE"
BAD = {
    TOKEN: "/opt/autofill/cf-api-token",
    ACCOUNT: "/opt/autofill/cf-account-id",
}
GOOD = {
    TOKEN: "/var/lib/autofill/cf-api-token",
    ACCOUNT: "/var/lib/autofill/cf-account-id",
}
MOUNT = "-v /opt/autofill/data:/var/lib/autofill"


def _assignments(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in (TOKEN, ACCOUNT):
        match = re.search(rf"^{name}=(\S+)\s*$", text, re.M)
        if match:
            found[name] = match.group(1)
    return found


def test_example_does_not_advertise_unmounted_host_paths():
    example = EXAMPLE.read_text()
    doc = DOC.read_text()
    for name, path in BAD.items():
        assert f"{name}={path}" not in example
        assert f"{name}={path}" not in doc


def test_credential_paths_are_under_the_mounted_directory():
    example = _assignments(EXAMPLE.read_text())
    script = RUN.read_text()
    doc = DOC.read_text()
    assert MOUNT in script
    mount_target = MOUNT.split(":", 1)[1]
    assert example == GOOD
    for path in example.values():
        assert path.startswith(mount_target + "/")
    for path in GOOD.values():
        assert path in doc
    assert "/opt/autofill/data/cf-api-token" in doc
    assert "/opt/autofill/data/cf-account-id" in doc
    assert "mode 600" in doc
