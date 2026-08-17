"""The negative control runs in CI, not only by hand.

A control nobody runs is a claim. This executes the script as a judge would —
as a subprocess, from the repository root, with the ClickHouse and Google Cloud
variables stripped from the environment, so the "offline and credential-free"
property is tested rather than asserted in a docstring.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "negative_control.py"

# Anything that could let the script reach real infrastructure by accident.
STRIPPED = (
    "CLICKHOUSE_HOST",
    "CLICKHOUSE_PORT",
    "CLICKHOUSE_USER",
    "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_DATABASE",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AGENT_ENGINE_RESOURCE",
)


def _run() -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED}
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_control_passes_with_no_credentials_present():
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_control_reports_every_arm_of_the_matrix():
    out = _run().stdout

    assert "Intact record" in out
    assert "C3_BOUNDARY" in out
    assert "Model rationale removed" in out
    assert "Snapshot binding removed" in out
    assert "Result hash mutated" in out
    assert "Original record unchanged" in out


def test_the_control_states_the_uncomfortable_result():
    """The finding is the deliverable, so it is asserted like one."""
    out = _run().stdout

    assert "changes nothing about verdict reproducibility" in out
    assert "deterministic evidence and policy determine the gate" in out
