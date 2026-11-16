#!/usr/bin/env python3
"""Install the shared Skill, host links, and merged guidance in one command."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from install_links import create_link, install_shared, preflight_hosts, validate_skill
from yaml_common import atomic_write_text

HOSTS = {
    "codex": (".codex/skills", ".codex/AGENTS.md"),
    "claude-code": (".claude/skills", ".claude/CLAUDE.md"),
    "kiro": (".kiro/skills", ".kiro/steering/AGENTS.md"),
    "deepseek": (".deepseek/skills", ".deepseek/instructions.md"),
}
START = "<!-- agent-workflow:start -->"
END = "<!-- agent-workflow:end -->"


def merge_guidance(template: Path, target: Path) -> str:
    existed = target.exists()
    incoming = template.read_text(encoding="utf-8").strip()
    if START not in incoming or END not in incoming:
        raise ValueError("指导模板缺少 agent-workflow 标记")
    if not target.exists():
        result = incoming + "\n"
    else:
        current = target.read_text(encoding="utf-8")
        starts = current.count(START)
        ends = current.count(END)
        if starts != ends or starts > 1:
            raise ValueError(f"现有指导标记不完整或重复: {target}")
        if starts == 1:
            start = current.index(START)
            end = current.index(END, start) + len(END)
            result = current[:start] + incoming + current[end:]
        else:
            result = current.rstrip() + "\n\n" + incoming + "\n"
    if target.exists():
        backup = target.with_name(f"{target.name}.backup-lucid-{uuid.uuid4().hex}")
        backup.write_bytes(target.read_bytes())
    atomic_write_text(target, result)
    return "guidance-merged" if existed else "guidance-created"


def selected_hosts(values: list[str]) -> list[str]:
    if "all" in values:
        return list(HOSTS)
    if "auto" in values:
        home = Path.home()
        return [name for name, (skills, _) in HOSTS.items() if (home / Path(skills).parent).exists()]
    return [value for value in values if value in HOSTS]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--shared-root", type=Path, default=Path.home() / ".agents")
    parser.add_argument(
        "--host",
        action="append",
        default=[],
        choices=("auto", "all", *HOSTS.keys(), "kimi", "kimi-code"),
        help="默认 auto；可重复，all 安装全部支持的宿主",
    )
    parser.add_argument("--use-existing", action="store_true", help="确认复用已有共享实体")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    actions = []
    try:
        source = arguments.source.expanduser().resolve()
        shared_root = arguments.shared_root.expanduser().resolve()
        target = shared_root / "skills" / "lucid-workflow"
        hosts = selected_hosts(arguments.host or ["auto"])
        host_dirs = [Path.home() / HOSTS[name][0] for name in hosts]
        validate_skill(source)
        preflight_hosts(host_dirs, target)
        actions.append({"action": install_shared(source, target, arguments.use_existing), "path": str(target)})
        template = source / "assets" / "AGENTS.template.md"
        for name in hosts:
            skills_dir, guidance = HOSTS[name]
            actions.append({"action": create_link(Path.home() / skills_dir, target), "path": str(Path.home() / skills_dir / "lucid-workflow")})
            actions.append({"action": merge_guidance(template, Path.home() / guidance), "path": str(Path.home() / guidance)})
        actions.append({"action": merge_guidance(template, shared_root / "AGENTS.md"), "path": str(shared_root / "AGENTS.md")})
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "actions": actions, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "actions": actions, "hosts": hosts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
