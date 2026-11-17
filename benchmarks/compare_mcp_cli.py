"""Compare current CLI and real MCP service on equivalent local workloads."""

from __future__ import annotations

import asyncio
import json
import math
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml
from mcp import Client

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "lucid-workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from coordination import service_control

ROUNDS = 3
TASKS_PER_ROUND = 60
FOREST_FILES = 50
WARM_QUERIES = 30


def summarize(values):
    ordered = sorted(values)
    return {
        "median_seconds": statistics.median(values),
        "mean_seconds": statistics.mean(values),
        "p95_seconds": ordered[math.ceil(len(values) * 0.95) - 1],
        "min_seconds": ordered[0],
        "max_seconds": ordered[-1],
    }


def run_cli(arguments):
    completed = subprocess.run(
        [sys.executable, "-B", *map(str, arguments)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)


def cli_workflow(project, count):
    todos = project / ".todos.yml"
    timings = {"add": [], "claim": [], "complete": []}
    total_start = time.perf_counter()
    run_cli([SCRIPTS / "state_files.py", "todos", "init", "--file", todos])
    for index in range(count):
        start = time.perf_counter()
        run_cli([
            SCRIPTS / "state_files.py", "todos", "add", "--file", todos,
            "--title", f"任务 {index}", "--acceptance-criteria", "验证通过",
        ])
        timings["add"].append(time.perf_counter() - start)
        start = time.perf_counter()
        run_cli([
            SCRIPTS / "state_files.py", "todos", "transition", "--file", todos,
            "--id", f"TASK-{index + 1:03d}", "--to", "working", "--executor", "agent-a",
        ])
        timings["claim"].append(time.perf_counter() - start)
        start = time.perf_counter()
        run_cli([
            SCRIPTS / "state_files.py", "todos", "transition", "--file", todos,
            "--id", f"TASK-{index + 1:03d}", "--to", "done", "--verification", "测试通过",
        ])
        timings["complete"].append(time.perf_counter() - start)
    timings["total"] = [time.perf_counter() - total_start]
    return timings


async def mcp_workflow(client, project, count):
    timings = {"add": [], "claim": [], "complete": []}
    total_start = time.perf_counter()
    for index in range(count):
        start = time.perf_counter()
        result = await client.call_tool(
            "tasks.add",
            {
                "project": project,
                "title": f"任务 {index}",
                "acceptance_criteria": ["验证通过"],
            },
        )
        if result.is_error:
            raise RuntimeError(result)
        timings["add"].append(time.perf_counter() - start)
        start = time.perf_counter()
        result = await client.call_tool(
            "tasks.claim_next",
            {"project": project, "agent": "agent-a", "session_id": "benchmark"},
        )
        if result.is_error or result.structured_content["task_id"] != f"TASK-{index + 1:03d}":
            raise RuntimeError(result)
        timings["claim"].append(time.perf_counter() - start)
        start = time.perf_counter()
        result = await client.call_tool(
            "tasks.complete",
            {
                "project": project,
                "agent": "agent-a",
                "task_id": f"TASK-{index + 1:03d}",
                "verification": "测试通过",
            },
        )
        if result.is_error:
            raise RuntimeError(result)
        timings["complete"].append(time.perf_counter() - start)
    timings["total"] = [time.perf_counter() - total_start]
    return timings


def create_forest(root):
    source = root / "src"
    source.mkdir(parents=True)
    (root / ".doc-forest").mkdir()
    (root / ".doc-forest.yml").write_text(
        'schema_version: "1.0"\nwrite_check:\n  excluded_paths: []\n'
        'forest:\n  allowlist: []\n  excluded_paths: []\n',
        encoding="utf-8",
    )
    (root / ".doc-forest" / "info.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "purpose": "基准项目",
                "contents_summary": "购买恢复模块",
                "directories": [{"name": "src"}],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    files = []
    for index in range(FOREST_FILES):
        name = f"module_{index:02d}.py"
        files.append({"name": name, "purpose": "购买恢复实现"})
        (source / name).write_text(
            f"def restore_purchase_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    (root / ".doc-forest" / "src").mkdir()
    (root / ".doc-forest" / "src" / "info.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "purpose": "购买恢复模块",
                "contents_summary": "购买恢复源码",
                "files": files,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def cli_locate(root):
    run_cli([
        SCRIPTS / "forest_context.py", "--root", root, "--task", "恢复购买",
        "--keyword", "restore", "--max-results", 10, "--format", "json",
    ])


async def mcp_locate(client, root):
    result = await client.call_tool(
        "forest.locate",
        {
            "project_root": str(root),
            "task": "恢复购买",
            "keywords": ["restore"],
            "max_results": 10,
        },
    )
    if result.is_error or not result.structured_content["report"]["locations"]:
        raise RuntimeError(result)


def rss_mib(pid):
    completed = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        return None
    return int(completed.stdout.strip()) / 1024


async def run_benchmarks(endpoint, base, service_startup, initial_rss):
    groups = {
        "cli": {"add": [], "claim": [], "complete": [], "total": [], "cold": [], "warm": []},
        "mcp": {"add": [], "claim": [], "complete": [], "total": [], "cold": [], "warm": []},
    }
    client_setup = []
    for round_index in range(ROUNDS):
        cli_project = base / f"cli-{round_index}"
        cli_project.mkdir()
        setup_start = time.perf_counter()
        async with Client(endpoint["url"]) as client:
            client_setup.append(time.perf_counter() - setup_start)
            cli_timing = lambda: cli_workflow(cli_project, TASKS_PER_ROUND)
            mcp_timing = lambda: mcp_workflow(client, f"mcp-{round_index}", TASKS_PER_ROUND)
            if round_index % 2 == 0:
                cli_result = cli_timing()
                mcp_result = await mcp_timing()
            else:
                mcp_result = await mcp_timing()
                cli_result = cli_timing()
            for key in ("add", "claim", "complete", "total"):
                groups["cli"][key].extend(cli_result[key])
                groups["mcp"][key].extend(mcp_result[key])

            forest_root = base / f"forest-{round_index}"
            create_forest(forest_root)
            if round_index % 2 == 0:
                start = time.perf_counter()
                cli_locate(forest_root)
                groups["cli"]["cold"].append(time.perf_counter() - start)
                start = time.perf_counter()
                await mcp_locate(client, forest_root)
                groups["mcp"]["cold"].append(time.perf_counter() - start)
            else:
                start = time.perf_counter()
                await mcp_locate(client, forest_root)
                groups["mcp"]["cold"].append(time.perf_counter() - start)
                start = time.perf_counter()
                cli_locate(forest_root)
                groups["cli"]["cold"].append(time.perf_counter() - start)
            for _ in range(WARM_QUERIES):
                start = time.perf_counter()
                cli_locate(forest_root)
                groups["cli"]["warm"].append(time.perf_counter() - start)
                start = time.perf_counter()
                await mcp_locate(client, forest_root)
                groups["mcp"]["warm"].append(time.perf_counter() - start)
    result = {
        "config": {
            "rounds": ROUNDS,
            "tasks_per_round": TASKS_PER_ROUND,
            "forest_files": FOREST_FILES,
            "warm_queries_per_round": WARM_QUERIES,
        },
        "environment": {
            "python": sys.version.split()[0],
            "mcp_sdk": "2.3.0",
            "protocol": "2026-07-28",
        },
        "service": {
            "startup": summarize([service_startup]),
            "client_setup": summarize(client_setup),
            "initial_rss_mib": initial_rss,
            "final_rss_mib": rss_mib(endpoint["pid"]),
        },
    }
    for side, values in groups.items():
        result[side] = {key: summarize(samples) for key, samples in values.items()}
    return result


def main():
    with tempfile.TemporaryDirectory(prefix="lucid-benchmark-") as directory:
        base = Path(directory)
        state = base / "service-state"
        start = time.perf_counter()
        started = service_control.ensure(state)
        startup = time.perf_counter() - start
        endpoint = started["endpoint"]
        initial_rss = rss_mib(endpoint["pid"])
        try:
            result = asyncio.run(run_benchmarks(endpoint, base, startup, initial_rss))
        finally:
            service_control.stop(state)
    target = Path(__file__).with_name("mcp-cli-results.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "task_workflow_median_seconds": {
            "cli": result["cli"]["total"]["median_seconds"],
            "mcp": result["mcp"]["total"]["median_seconds"],
        },
        "warm_query_median_seconds": {
            "cli": result["cli"]["warm"]["median_seconds"],
            "mcp": result["mcp"]["warm"]["median_seconds"],
        },
        "service": result["service"],
        "result_file": str(target),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
