# CLI 与 MCP 本地性能对比

## 方法

基准脚本为 `compare_mcp_cli.py`，完整数据为 `mcp-cli-results.json`。使用 Python 3.12.16、官方 `mcp==2.3.0`、协议 2026-07-28、PyYAML 6.0.3，以及同一工作区代码。

任务流在每轮使用 60 个任务，每个任务执行新增、认领和完成，共三轮。CLI 每次操作启动一个新 Python 子进程并读写现有 YAML／JSONL；MCP 使用一个真实单实例 Streamable HTTP 服务和持久客户端。每轮交替两边的执行顺序。

Forest 用例包含 50 个源码文件，每轮分别测量首次冷缓存定位，再进行 30 次热缓存定位。MCP 与 CLI 使用相同检索实现和查询条件，缓存位置分别符合两种模式的实际设计。

结果是当前机器、当前合成负载下的单次基准过程，不是跨机器或生产负载保证。未测量真实模型推理时间、远程网络、宿主 UI 或真实 Agent 行为。

## 任务与检索结果

以下使用中位数，完整均值、p95、最小值和最大值见 JSON。

| 指标 | CLI | MCP | 时间比：CLI／MCP |
| --- | ---: | ---: | ---: |
| 60 个任务完整流程 | 9.750 s | 0.261 s | 约 37.4 倍 |
| 新增任务，单次 | 47.07 ms | 1.36 ms | 约 34.6 倍 |
| 认领任务，单次 | 57.29 ms | 1.35 ms | 约 42.4 倍 |
| 完成任务，单次 | 58.09 ms | 1.31 ms | 约 44.2 倍 |
| Forest 冷缓存定位 | 48.77 ms | 16.14 ms | 约 3.0 倍 |
| Forest 热缓存定位 | 37.60 ms | 5.08 ms | 约 7.4 倍 |

换算后，本次完整任务流的 MCP 耗时约为 CLI 的 2.7%；热缓存查询约为 13.5%。

主要差异符合实现结构：CLI 每次操作承担 Python 进程启动、模块导入、整份 YAML 校验与原子替换及 JSONL 审计；MCP 复用进程和客户端，并通过 SQLite 事务写入。该结果不能解释为 MCP 协议本身具有普遍的固定加速倍数。

## 服务成本

| 指标 | 实测 |
| --- | ---: |
| 首次服务启动及健康检查 | 0.467 s |
| 客户端建立连接，中位数 | 1.83 ms |
| 服务启动后 RSS | 73.33 MiB |
| 基准结束时 RSS | 74.47 MiB |

MCP 在本次操作中明显降低耗时，但代价是需要常驻服务及约 73 MiB 的初始常驻内存。CLI 没有常驻服务内存，但本次未测量其子进程的瞬时内存，因此不做总内存优劣结论。

## 适用范围与后续

该基准支持“少量到中等数量本地 Agent、频繁状态写入和重复 Forest 查询时，常驻 MCP 服务可以显著减少操作开销”的判断。

仍需单独验证长期事件增长、滚动保留、分页、精简 bootstrap、空闲 CPU、真实项目规模、远程网络和宿主工具 Schema 注入成本。当前结果不替代这些验收，也不授权默认启用或真实 Agent 接入。

## 复现

```bash
mkdir -p tmp
python3 -m venv tmp/mcp-venv
tmp/mcp-venv/bin/python -m pip install --disable-pip-version-check \
  -r lucid-workflow/scripts/coordination/requirements.txt
TMPDIR="$PWD/tmp" tmp/mcp-venv/bin/python -B benchmarks/compare_mcp_cli.py
```

结束后删除临时环境：

```bash
rm -rf tmp
```
