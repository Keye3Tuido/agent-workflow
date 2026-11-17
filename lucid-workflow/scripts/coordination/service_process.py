"""Experimental single-instance MCP background service."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import uvicorn

from .mcp_server import create_server


class InstanceAlreadyRunning(RuntimeError):
    pass


class SingletonLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if handle.read(1) == b"":
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise InstanceAlreadyRunning("instance_already_running") from exc
        self.handle = handle

    def release(self) -> None:
        if self.handle is None:
            return
        handle = self.handle
        self.handle = None
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def publish_endpoint(path: Path, content: dict) -> None:
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".endpoint-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(content, temporary, ensure_ascii=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def remove_own_endpoint(path: Path, instance_id: str) -> None:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return
    if content.get("instance_id") == instance_id:
        path.unlink(missing_ok=True)


async def serve(state_directory: Path) -> None:
    lock = SingletonLock(state_directory / "service.lock")
    lock.acquire()
    instance_id = uuid.uuid4().hex
    endpoint_path = state_directory / "endpoint.json"
    server_task = None
    try:
        mcp = create_server(state_directory / "coordination.sqlite")
        app = mcp.streamable_http_app(json_response=True, stateless_http=True)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="error",
            access_log=False,
        )
        http_server = uvicorn.Server(config)
        server_task = asyncio.create_task(http_server.serve())
        for _ in range(100):
            if http_server.started:
                break
            if server_task.done():
                exception = server_task.exception()
                raise exception or RuntimeError("service exited before startup")
            await asyncio.sleep(0.05)
        if not http_server.started:
            raise TimeoutError("service startup timed out")
        socket = http_server.servers[0].sockets[0]
        publish_endpoint(
            endpoint_path,
            {
                "url": f"http://127.0.0.1:{socket.getsockname()[1]}/mcp",
                "pid": os.getpid(),
                "instance_id": instance_id,
                "protocol_version": "2026-07-28",
                "service_version": mcp.version,
            },
        )
        await server_task
    finally:
        if server_task is not None and not server_task.done():
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
        remove_own_endpoint(endpoint_path, instance_id)
        lock.release()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        asyncio.run(serve(arguments.state_dir.resolve()))
    except InstanceAlreadyRunning:
        print(json.dumps({"ok": False, "error": "instance_already_running"}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
