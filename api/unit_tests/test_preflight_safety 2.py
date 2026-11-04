"""Preflight must never deploy a stack or remove its volumes."""

import importlib.util
import json
from pathlib import Path
from subprocess import CompletedProcess


def test_image_check_uses_no_network_or_volumes(monkeypatch):  # type: ignore[no-untyped-def]
    path = Path(__file__).resolve().parents[2] / "infrastructure/scripts/preflight.py"
    spec = importlib.util.spec_from_file_location("readiness_preflight", path)
    assert spec is not None and spec.loader is not None
    preflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preflight)
    calls = []

    def run(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((command, kwargs))
        payload = json.dumps({"services": {"api": {"environment": {"JWT_SECRET": "private-test-value"}}}})
        return CompletedProcess(command, 0, stdout=payload if "config" in command else "", stderr="")

    monkeypatch.setattr(preflight, "run", run)
    preflight.check_secrets({}, ["docker-compose.yml"], "staging.env", False, "review-api:test")
    assert len(calls) == 2
    command, kwargs = calls[1]
    assert command[:4] == ["docker", "run", "--rm", "-i"]
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command and "--cap-drop" in command
    assert not {"up", "down", "build", "-v", "--volume"}.intersection(command)
    assert "private-test-value" not in " ".join(command)
    assert json.loads(kwargs["input"])["JWT_SECRET"] == "private-test-value"  # noqa: S105 -- sentinel, not a credential
