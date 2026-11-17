"""Install Lucid Workflow with its recommended single-instance MCP service."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import venv
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from coordination.service_control import default_state_directory


def run_json(arguments: list[str], environment: dict[str, str]) -> dict[str, Any]:
    completed = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"服务管理命令没有返回有效 JSON: {completed.stdout!r}") from exc


def service_python(venv_directory: Path) -> Path:
    return venv_directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def install(state_directory: Path, host: str = "auto") -> dict[str, Any]:
    state_directory = state_directory.resolve()
    state_directory.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    completed = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "install.py"), "--host", host, "--use-existing"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())

    venv_directory = state_directory / "venv"
    python = service_python(venv_directory)
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(venv_directory)
    requirements = SKILL_ROOT / "scripts" / "coordination" / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = state_directory / "requirements.sha256"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != digest:
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-q",
                "-r",
                str(requirements),
            ],
            env=environment,
            check=True,
        )
        marker.write_text(digest + "\n", encoding="utf-8")

    control = SCRIPTS / "coordination" / "service_control.py"
    status = run_json([str(python), "-B", str(control), "status", "--state-dir", str(state_directory)], environment)
    current_version = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if status.get("running"):
        endpoint = status.get("endpoint", {})
        if endpoint.get("service_version") != current_version:
            run_json([str(python), "-B", str(control), "stop", "--state-dir", str(state_directory)], environment)
    started = run_json([str(python), "-B", str(control), "ensure", "--state-dir", str(state_directory)], environment)
    return {
        "service_version": current_version,
        "action": started["action"],
        "endpoint": started["endpoint"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="auto")
    parser.add_argument("--state-dir", type=Path, default=default_state_directory())
    arguments = parser.parse_args()
    try:
        result = install(arguments.state_dir, arguments.host)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
