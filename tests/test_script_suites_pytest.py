"""Pytest wrappers for script-style offline suites (dual-run with run-tests.sh).

These modules use a PASS/FAIL harness and raise SystemExit when executed as
scripts. Under pytest we invoke them as subprocesses so import-time execution
does not abort collection.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent

SCRIPT_SUITES = [
    "test_mcp_stub.py",
    "test_send.py",
    "test_poll.py",
    "test_tts_package.py",
    "test_project_bind.py",
    "test_providers_plugplay.py",
    "test_extensions_adk.py",
]


@pytest.mark.parametrize("script", SCRIPT_SUITES)
def test_script_suite_exits_zero(script: str) -> None:
    path = TESTS / script
    assert path.is_file(), path
    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"{script} exited {proc.returncode}\n"
            f"stdout:\n{proc.stdout[-2000:]}\n"
            f"stderr:\n{proc.stderr[-2000:]}"
        )
