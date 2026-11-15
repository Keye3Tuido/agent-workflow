#!/usr/bin/env python3
"""Check the latest GitHub release and safely update the shared Skill."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


REPOSITORY = "Keye3Tuido/agent-workflow"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET_NAME = "agent-workflow.skill"
MAX_PACKAGE_BYTES = 20 * 1024 * 1024


def parse_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    if not match:
        raise ValueError(f"版本必须是 v?X.Y.Z: {value!r}")
    return tuple(int(part) for part in match.groups())


def installed_version(installed_root: Path) -> str:
    path = installed_root / "VERSION"
    if not path.is_file():
        return "0.0.0"
    return path.read_text(encoding="utf-8").strip()


def fetch_latest_release(api_url: str = RELEASE_API) -> dict[str, Any]:
    request = urllib.request.Request(api_url, headers={"User-Agent": "agent-workflow-update"})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    if not isinstance(release, dict) or not release.get("tag_name"):
        raise ValueError("最新 release 缺少 tag_name")
    assets = release.get("assets", [])
    asset = next((item for item in assets if item.get("name") == ASSET_NAME), None)
    if not asset:
        raise ValueError(f"最新 release 缺少资产 {ASSET_NAME}")
    url = asset.get("browser_download_url", "")
    expected_prefix = f"https://github.com/{REPOSITORY}/releases/download/"
    if not url.startswith(expected_prefix):
        raise ValueError("发布资产地址不属于预期仓库")
    return {
        "version": release["tag_name"],
        "asset_url": url,
        "release_url": release.get("html_url") or f"https://github.com/{REPOSITORY}/releases/latest",
    }


def inspect_update(installed_root: Path, release: dict[str, Any]) -> dict[str, Any]:
    current = installed_version(installed_root)
    latest = release["version"]
    parse_version(current)
    parse_version(latest)
    return {
        "current": current,
        "latest": latest,
        "update_available": parse_version(latest) > parse_version(current),
        "release_url": release["release_url"],
        "asset_url": release["asset_url"],
    }


def download_package(url: str, directory: Path) -> Path:
    if not url.startswith(f"https://github.com/{REPOSITORY}/releases/download/"):
        raise ValueError("拒绝非预期仓库的下载地址")
    request = urllib.request.Request(url, headers={"User-Agent": "agent-workflow-update"})
    package = directory / ASSET_NAME
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read(MAX_PACKAGE_BYTES + 1)
    if len(content) > MAX_PACKAGE_BYTES:
        raise ValueError("发布资产超过 20 MB 限制")
    package.write_bytes(content)
    return package


def validate_package(package: Path, expected_version: str) -> None:
    if not zipfile.is_zipfile(package):
        raise ValueError("发布资产不是合法 ZIP 包")
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "agent-workflow":
                raise ValueError(f"压缩包包含不安全路径: {info.filename}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("压缩包不得包含符号链接")
        for required in ("agent-workflow/SKILL.md", "agent-workflow/VERSION"):
            if required not in names:
                raise ValueError(f"压缩包缺少 {required}")
        version = archive.read("agent-workflow/VERSION").decode("utf-8").strip()
        if parse_version(version) != parse_version(expected_version):
            raise ValueError(f"包版本 {version} 与 release {expected_version} 不一致")


def apply_package(package: Path, installed_root: Path, expected_version: str) -> dict[str, Any]:
    validate_package(package, expected_version)
    installed_root.parent.mkdir(parents=True, exist_ok=True)
    if installed_root.is_symlink():
        raise ValueError("共享 Skill 根目录不得是符号链接")
    temporary = Path(tempfile.mkdtemp(prefix=".agent-workflow-update-", dir=installed_root.parent))
    backup = None
    try:
        with zipfile.ZipFile(package) as archive:
            archive.extractall(temporary)
        source = temporary / "agent-workflow"
        if installed_root.exists():
            backup = installed_root.parent / f".agent-workflow.backup-{uuid.uuid4().hex}"
            os.replace(installed_root, backup)
        try:
            os.replace(source, installed_root)
        except Exception:
            if backup and backup.exists() and not installed_root.exists():
                os.replace(backup, installed_root)
            raise
        return {
            "updated": True,
            "version": installed_version(installed_root),
            "backup": str(backup) if backup else None,
        }
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "apply"))
    parser.add_argument(
        "--installed-root",
        type=Path,
        default=Path("~/.agents/skills/agent-workflow"),
    )
    arguments = parser.parse_args()
    try:
        root = arguments.installed_root.expanduser().resolve()
        release = fetch_latest_release()
        status = inspect_update(root, release)
        if arguments.action == "check" or not status["update_available"]:
            print(json.dumps(status, ensure_ascii=False))
            return 0
        with tempfile.TemporaryDirectory(prefix="agent-workflow-download-") as directory:
            package = download_package(release["asset_url"], Path(directory))
            result = apply_package(package, root, release["version"])
        print(json.dumps({**status, **result}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
