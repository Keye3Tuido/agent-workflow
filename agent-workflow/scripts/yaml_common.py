#!/usr/bin/env python3
"""Strict YAML, timestamp, locking, and atomic-write helpers."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode


class YamlError(ValueError):
    pass


class StrictLoader(yaml.SafeLoader):
    pass


def normalize_project_path(value: str, allow_root: bool = False) -> str:
    if allow_root and value == ".":
        return "."
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise ValueError(f"无效项目相对路径: {value!r}")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"无效项目相对路径: {value!r}")
    path = PurePosixPath(value)
    if any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"无效项目相对路径: {value!r}")
    return path.as_posix()


def _construct_mapping(loader: StrictLoader, node: MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def load_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise YamlError(f"{path}: 必须使用 UTF-8") from exc
    try:
        for event in yaml.parse(text, Loader=StrictLoader):
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None):
                raise YamlError(f"{path}: 禁止 YAML 锚点和别名")
        return yaml.load(text, Loader=StrictLoader)
    except YamlError:
        raise
    except yaml.YAMLError as exc:
        raise YamlError(f"{path}: {exc}") from exc


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    )


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else None
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
        if mode is not None:
            os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
        temporary_name = None
        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def atomic_write_yaml(path: Path, data: Any) -> None:
    atomic_write_text(path, dump_yaml(data))


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Lock a stable sidecar so atomic replacement never changes the lock target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            if lock_file.read(1) == b"":
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def require_keys(
    value: Any,
    required: set[str],
    optional: set[str] | None = None,
    context: str = "mapping",
) -> list[str]:
    if not isinstance(value, dict):
        return [f"{context} 必须是映射"]
    optional = optional or set()
    errors = [f"{context} 缺少字段 {key!r}" for key in sorted(required - value.keys())]
    errors.extend(
        f"{context} 含未知字段 {key!r}"
        for key in sorted(value.keys() - required - optional, key=str)
    )
    return errors


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
