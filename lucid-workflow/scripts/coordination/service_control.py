"""Manage the optional single-instance MCP coordination service."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SERVICE_NAME = "lucid-workflow-coordination-poc"
PROTOCOL_VERSION = "2026-07-28"
SCRIPTS = Path(__file__).resolve().parent.parent
_CHILDREN: dict[int, subprocess.Popen] = {}


def default_state_directory() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "agent-workflow" / "service"
    return Path.home() / ".local" / "state" / "agent-workflow" / "service"


def load_endpoint(state_directory: Path) -> dict[str, Any]:
    endpoint = json.loads((state_directory / "endpoint.json").read_text(encoding="utf-8"))
    if not isinstance(endpoint, dict):
        raise ValueError("服务端点格式无效")
    if not isinstance(endpoint.get("url"), str) or not isinstance(endpoint.get("instance_id"), str):
        raise ValueError("服务端点缺少 URL 或实例 ID")
    if not isinstance(endpoint.get("pid"), int) or endpoint["pid"] < 1:
        raise ValueError("服务端点 PID 无效")
    if endpoint.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("服务端点协议版本不匹配")
    return endpoint


async def verify(endpoint: dict[str, Any]) -> dict[str, Any]:
    from mcp import Client

    async with Client(endpoint["url"]) as client:
        await client.list_tools()
        if client.protocol_version != PROTOCOL_VERSION or client.server_info.name != SERVICE_NAME:
            raise ValueError("端点不是预期的 Lucid Workflow 协调服务")
        if endpoint.get("service_version") != client.server_info.version:
            raise ValueError("端点记录与服务实际版本不一致")
    return endpoint


def verified_endpoint(state_directory: Path) -> dict[str, Any]:
    return asyncio.run(verify(load_endpoint(state_directory)))


def launch(state_directory: Path) -> subprocess.Popen:
    state_directory.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(SCRIPTS) + (os.pathsep + existing if existing else "")
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with (state_directory / "service.log").open("ab", buffering=0) as log:
        process = subprocess.Popen(
            [sys.executable, "-B", "-m", "coordination.service_process", "--state-dir", str(state_directory)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=environment,
            close_fds=True,
            **options,
        )
    _CHILDREN[process.pid] = process
    return process


def ensure(state_directory: Path, timeout: float = 20) -> dict[str, Any]:
    state_directory = state_directory.resolve()
    try:
        endpoint = verified_endpoint(state_directory)
        return {"action": "existing", "endpoint": endpoint}
    except Exception:
        pass
    process = launch(state_directory)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            try:
                endpoint = verified_endpoint(state_directory)
                return {"action": "started", "endpoint": endpoint}
            except Exception:
                time.sleep(0.1)
            if process.poll() is not None and not (state_directory / "endpoint.json").exists():
                break
        raise TimeoutError("协调服务启动或健康检查超时")
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        raise
    finally:
        if process.poll() is not None:
            _CHILDREN.pop(process.pid, None)


def status(state_directory: Path) -> dict[str, Any]:
    try:
        endpoint = verified_endpoint(state_directory.resolve())
        return {"running": True, "endpoint": endpoint}
    except Exception as exc:
        return {"running": False, "error": type(exc).__name__}


def stop(state_directory: Path, timeout: float = 10) -> dict[str, Any]:
    endpoint = verified_endpoint(state_directory.resolve())
    pid = endpoint["pid"]
    os.kill(pid, signal.SIGTERM)
    process = _CHILDREN.pop(pid, None)
    if process is not None:
        process.wait(timeout=timeout)
    return {"stopped": True, "instance_id": endpoint["instance_id"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("ensure", "status", "stop"))
    parser.add_argument("--state-dir", type=Path, default=default_state_directory())
    arguments = parser.parse_args()
    try:
        if arguments.action == "ensure":
            result = ensure(arguments.state_dir)
        elif arguments.action == "status":
            result = status(arguments.state_dir)
        else:
            result = stop(arguments.state_dir)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TimeoutError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
