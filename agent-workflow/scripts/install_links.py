#!/usr/bin/env python3
"""Install one shared agent-workflow Skill and host-native directory links."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


class InstallError(ValueError):
    pass


def validate_skill(path: Path) -> None:
    skill_file = path / "SKILL.md"
    if not path.is_dir() or not skill_file.is_file():
        raise InstallError(f"不是有效 Skill 目录: {path}")
    text = skill_file.read_text(encoding="utf-8")
    if not re.search(r"^name:\s*agent-workflow\s*$", text, re.MULTILINE):
        raise InstallError("SKILL.md 的 name 必须是 agent-workflow")


def install_shared(source: Path, target: Path, use_existing: bool) -> str:
    if target.exists():
        validate_skill(target)
        try:
            same = source.samefile(target)
        except OSError:
            same = False
        if same:
            return "existing-authoritative"
        if not use_existing:
            raise InstallError(f"共享实体已存在；确认复用时加 --use-existing: {target}")
        return "existing-reused"
    if source == target or target in source.parents:
        raise InstallError("共享目标不能包含当前 Skill 源目录")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".agent-workflow.", dir=target.parent))
    try:
        shutil.copytree(
            source,
            temporary,
            dirs_exist_ok=True,
            symlinks=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
        )
        validate_skill(temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return "installed"


def create_link(host_skill_dir: Path, target: Path) -> str:
    host_skill_dir.mkdir(parents=True, exist_ok=True)
    link = host_skill_dir / "agent-workflow"
    if link.is_symlink():
        if link.resolve() == target.resolve():
            return "already-linked"
        raise InstallError(f"已有链接指向其他实体: {link}")
    if link.exists():
        raise InstallError(f"宿主位置已存在文件或目录，不覆盖: {link}")
    os.symlink(target, link, target_is_directory=True)
    return "linked"


def preflight_hosts(hosts: list[Path], target: Path) -> None:
    seen = set()
    for host in hosts:
        link = host / "agent-workflow"
        key = str(link.absolute())
        if key in seen:
            raise InstallError(f"重复宿主链接: {link}")
        seen.add(key)
        if link.is_symlink():
            if link.resolve() != target.resolve():
                raise InstallError(f"已有链接指向其他实体: {link}")
        elif link.exists():
            raise InstallError(f"宿主位置已存在文件或目录，不覆盖: {link}")


def main() -> int:
    default_source = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=default_source, help="待安装的 Skill 源目录")
    parser.add_argument("--shared-root", type=Path, default=Path("~/.agents"), help="共享 .agents 根目录")
    parser.add_argument("--host-skill-dir", type=Path, action="append", default=[], help="宿主 Skill 目录，可重复")
    parser.add_argument("--use-existing", action="store_true", help="显式复用已经存在的共享实体")
    arguments = parser.parse_args()
    actions = []
    try:
        source = arguments.source.expanduser().resolve()
        shared_root = arguments.shared_root.expanduser().resolve()
        target = shared_root / "skills" / "agent-workflow"
        hosts = [path.expanduser().resolve() for path in arguments.host_skill_dir]
        validate_skill(source)
        preflight_hosts(hosts, target)
        shared_action = install_shared(source, target, arguments.use_existing)
        actions.append({"action": shared_action, "path": str(target)})
        for host in hosts:
            action = create_link(host, target)
            actions.append({"action": action, "path": str(host / "agent-workflow")})
    except (InstallError, OSError) as exc:
        print(
            json.dumps({"ok": False, "actions": actions, "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"ok": True, "actions": actions}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
