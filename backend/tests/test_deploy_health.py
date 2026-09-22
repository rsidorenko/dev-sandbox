"""Проверяем коды выхода реального скрипта деплоя без обращения к серверу."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("failures", "expected_code", "expected_calls"), [(0, 0, 1), (2, 0, 3), (20, 1, 12)])
def test_healthcheck_retries_and_fails_closed(tmp_path, failures, expected_code, expected_calls):
    curl = tmp_path / "curl"
    curl.write_text(
        '#!/bin/bash\nn=0\n[ ! -f "$AUDIT_CALLS" ] || n=$(cat "$AUDIT_CALLS")\n'
        'n=$((n+1))\necho "$n" > "$AUDIT_CALLS"\n'
        '[[ "$*" == *"--max-time 5"* ]] || exit 99\n'
        '[[ "$*" == *"http://localhost:12345/healthz"* ]] || exit 99\n'
        f'[ "$n" -gt {failures} ]\n'
    )
    curl.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text("#!/bin/bash\nexit 0\n")
    sleep.chmod(0o755)
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/ci/verify_health.sh"), "http://localhost:12345/healthz"],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "AUDIT_CALLS": str(calls)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == expected_code
    assert int(calls.read_text()) == expected_calls
