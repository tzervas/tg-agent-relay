"""pytest configuration for TG Agent Relay pure-Python unit tests.

Primary unit path (issue #29)
-----------------------------
  uv run pytest
  uv run pytest tests/ -q

Each ``tests/test_*.py`` module is dual-run:
  - ``def test_*()`` — collected by pytest (calls ``main()``)
  - ``python tests/test_*.py`` / ``relay_python tests/test_*.py`` — standalone
    script still used by ``tests/run-tests.sh``

Shell / e2e smoke (optional; not pytest)
----------------------------------------
  bash tests/run-tests.sh

``run-tests.sh`` remains the offline shell + install-hooks + adapter e2e
suite (mock tg-send, no network). Prefer pytest for pure Python modules;
keep the bash runner for integration smoke until those ports land.

No network in this package's unit tests.
"""

from __future__ import annotations

# Intentionally minimal: path setup lives in pyproject.toml
#   [tool.pytest.ini_options] pythonpath = [".", "lib"]
# so imports of tg_agent_relay / providers / lib/* resolve under uv run pytest.
