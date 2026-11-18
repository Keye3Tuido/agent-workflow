# Lucid Workflow

> **让 AI 少被打断，安心做开发。**

[![Latest Release](https://img.shields.io/github/v/release/Keye3Tuido/lucid-workflow?display_name=tag)](https://github.com/Keye3Tuido/lucid-workflow/releases/latest)
[![License: MIT](https://img.shields.io/github/license/Keye3Tuido/lucid-workflow)](LICENSE)

## 它解决什么问题？

AI 写代码时，经常不是不会写，而是现场太容易乱：

- 对话一长，前面的要求被截断；
- 工作中断后，做到哪一步说不清；
- 多个 Agent 各做各的，进度对不上；
- 只是改一个小问题，却读进一大堆无关文件。

Lucid Workflow 把要求、进度和相关代码的位置整理好。模型不用靠记忆硬撑，也不用每次都从头开始。

这里说的“纯净”，不是把 AI 与外界隔绝，而是减少那些本可以避免的干扰：找不到信息、忘记要求、进度被覆盖、做完又从头再来。

## 它怎么帮到你？

### 找代码，不用翻遍仓库

Forest 会根据当前任务找出相关模块、源码、测试和具体行号。它会利用已有的模块说明和缓存，但不会把缓存当成事实来源。

找到位置后，仍然要读源码和测试确认。关键词命中只是线索，不是结论。模块结构和维护需求变化时，Forest 会按需深化或折叠，不为追求目录完整而增加无效层级。

### 写说明，不堆套话

做了什么、结果怎样、哪里还没确定，直接说清楚。重复的话删掉，必要的条件和限制保留下来。

无论是项目文档还是一次工作回复，都以读者能不能看懂、能不能继续操作为准。

### 要求记下来，不靠模型记性

用户提出的要求、禁止项、适用范围和失效条件都会写入当前会话。要求变了就更新，取消或到期就删除。每次答复前再检查一遍，做到什么程度、证据是什么，都有明确记录，而不是依赖模型“应该还记得”。

### 不确定就问你，不替你猜

能通过源码、文档或工具确认的事情，先自己查证。真正影响目标、权限、安全或结果正确性的缺口，再向你提问。

每次只问一个关键问题，并给出建议或明确选项。问题、必要上下文和你的回答都会保留下来，即使对话截断或换了 Agent，也能找到这个决定。

以最少打扰为原则：已明确的授权、验收和范围不重复确认，一句话同时说明验收和提交时直接按范围执行，不拆成多轮；但不会从“做得好”推断提交或发布权限。常规提交沿用原始授权，没有授权时最多问一次。相关决策集中处理，后台检查和维护成功时不额外打扰。

### 工作停了，也能接着做

任务记录会保存谁在做什么、任务之间的依赖、验证结果和用户是否验收。多个 Agent 可以共享进度，不同会话的临时要求仍保持独立。

遇到上下文截断或工作中断，可以从已有记录继续。用户肯定对应成果后即可验收；准许提交也视为对本次提交覆盖成果的验收。旧任务集中复核，不因时间或数量自动验收、删除。

## 默认推荐 MCP 方式

新项目默认推荐使用 MCP：由持续可用的协调服务统一保存进度、协调多个 Agent，并提供结构化工具。Agent 不需要逐项调用脚本，常规后台维护保持静默。

既有 CLI 项目不会被自动迁移，也不会在服务异常时静默切换到另一套写入状态。CLI 继续用于既有项目、离线管理和显式迁移。

## 快速开始

### 1. 下载并解包

从 [最新 Release](https://github.com/Keye3Tuido/lucid-workflow/releases/latest) 下载 `lucid-workflow.skill`，然后运行：

```bash
mkdir -p ./forest-package
unzip lucid-workflow.skill -d ./forest-package
```

### 2. 一条命令完成默认安装

```bash
python3 ./forest-package/lucid-workflow/scripts/coordination/install_mcp.py
```

这条命令会准备默认 MCP 服务、安装共享 Skill，并复用或启动唯一的后台实例。首次使用时，按宿主支持的方式注册返回的服务地址；已有宿主规则会保留。

支持 Codex、Claude Code、Kiro、DeepSeek、Kimi 和 Kimi Code 的 Skill 接入。MCP 的实际使用以宿主对 Streamable HTTP 的支持和真实加载结果为准。

### 3. 开始第一个任务

在已接入的 Agent 会话中输入：

```text
记录本次要求和禁止项，拆分任务，并定位“购买恢复”相关实现与测试。
```

Agent 会在需要时恢复状态、协调任务、定位代码并进行提交前检查。已有 CLI 项目可以继续使用原命令，不需要因为这次默认推荐而迁移。

## 它不会保证什么？

Lucid Workflow 不能保证模型永远正确，也不能阻止所有网络故障。它做的是把重要信息留好，让问题发生后能查、能恢复。

更新查询直接使用公开 API。无更新或更新成功时不发送常规聊天提示；网络更新失败时，会继续使用当前已安装版本并说明情况，不会把查询失败当成更新成功。

## 文档

- [Skill 操作说明](lucid-workflow/SKILL.md)
- [MCP 使用与生命周期](lucid-workflow/references/mcp-service.md)
- [路径与文件生命周期](lucid-workflow/references/paths.md)
- [YAML 规范与命令](lucid-workflow/references/yaml-schemas.md)
- [Forest 内容摘写指南](lucid-workflow/references/content-guidance.md)
- [验证记录](lucid-workflow/references/mcp-validation.md)
- [CLI 与 MCP 性能对比](benchmarks/mcp-cli-performance.md)

## 开发验证

```bash
python3 -B -m unittest discover -s tests -v
```

测试覆盖森林检查、上下文检索、状态并发、依赖与审计、多 Agent 协同、安装链接、更新安全、真实 MCP 调用和单实例生命周期。真实 MCP 测试的依赖安装与复现命令见验证记录；缺少对应依赖时会明确跳过，不计为通过。

## License

[MIT](LICENSE)
