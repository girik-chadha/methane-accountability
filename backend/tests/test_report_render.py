"""Runs web/tests/report.test.mjs: the committed standalone page rendered in
jsdom, USA_S_1063's report pinned to the exported values. Needs node and
`npm install` in web/ (jsdom, dev only). Skips only when node is absent; a
missing jsdom is a failure, not a skip."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"


def test_rendered_report_matches_export() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the rendered-report pin cannot run")
    assert (WEB / "node_modules" / "jsdom").exists(), "run `npm install` in web/ (jsdom, dev only)"
    run = subprocess.run([node, str(WEB / "tests" / "report.test.mjs")], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr or run.stdout
    assert "all from the export" in run.stdout
