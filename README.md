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

项目名称为 Lucid Workflow；既定 Skill 标识和发布资产仍使用 `agent-workflow`，避免影响已有安装与升级。

## 它怎么帮到你？

### 找代码，不用翻遍仓库

Forest 会根据当前任务找出相关模块、源码、测试和具体行号。它会利用已有的模块说明和缓存，但不会把缓存当成事实来源。

找到位置后，仍然要读源码和测试确认。关键词命中只是线索，不是结论。

### 写说明，不堆套话

做了什么、结果怎样、哪里还没确定，直接说清楚。重复的话删掉，必要的条件和限制保留下来。

无论是项目文档还是一次工作回复，都以读者能不能看懂、能不能继续操作为准。

### 要求记下来，不靠模型记性

用户提出的要求、禁止项、适用范围和失效条件都会写入当前会话的 `requirements.yml`。

要求变了就更新，取消或到期就删除。每次答复前再检查一遍，做到什么程度、证据是什么，都有明确记录，而不是依赖模型“应该还记得”。

### 不确定就问你，不替你猜

能通过源码、文档或工具确认的事情，先自己查证。真正影响目标、权限、安全或结果正确性的缺口，再向你提问。

每次只问一个关键问题，并给出建议或明确选项。问题、必要上下文和你的回答都会写入会话目录的 `questions.yml`，即使对话截断或换了 Agent，也能找到这个决定。

### 工作停了，也能接着做

共享 `.todos.yml` 会记录谁在做什么、任务之间的依赖、验证结果和用户是否验收。

多个 Agent 可以同时工作：任务进度共享，会话要求彼此独立。文件锁和原子替换避免并发写入把别人的进度覆盖掉。

遇到上下文截断或工作中断，读取这些记录就能继续。近期操作也会留下审计；超过 100 个完整操作时，清理到最近 50 个，不让日志越积越多。

## 一次工作大致是这样

```text
明确要求 → 记录任务 → 找到相关代码 → 开发与验证 → 用户验收
    ↑                                             ↓
    └──── 对话截断或工作中断后，从已有记录继续 ────┘
```

## 快速开始

### 1. 下载并解包

从 [最新 Release](https://github.com/Keye3Tuido/lucid-workflow/releases/latest) 下载 `agent-workflow.skill`，然后运行：

```bash
mkdir -p ./forest-package
unzip agent-workflow.skill -d ./forest-package
```

### 2. 一条命令完成安装

```bash
python3 ./forest-package/agent-workflow/scripts/install.py --host auto
```

这条命令会安装共享 Skill、检测已有宿主、建立链接并合并指导规则，同时保留其他规则。使用 `--host all` 可安装所有支持的宿主；已有共享实体需要复用时添加 `--use-existing`。

支持 Codex、Claude Code、Kiro、DeepSeek、Kimi 和 Kimi Code。Kimi／Kimi Code 在新会话或重启后刷新。

### 3. 开始第一个任务

在已接入的 Agent 会话中输入：

```text
记录本次要求和禁止项，拆分任务，并使用 Forest 定位“购买恢复”相关实现与测试。
```

也可以直接运行上下文定位器：

```bash
python3 "$HOME/.agents/skills/agent-workflow/scripts/forest_context.py" \
  --root /path/to/project \
  --task "修复购买恢复逻辑" \
  --keyword "restore" \
  --max-results 10
```

## 它不会保证什么？

Lucid Workflow 不能保证模型永远正确，也不能阻止所有网络故障。它做的是把重要信息留好，让问题发生后能查、能恢复。

网络更新失败时，会继续使用当前已安装版本并说明情况，不会把查询失败当成更新成功。

## 文档

- [Skill 操作说明](agent-workflow/SKILL.md)
- [路径与文件生命周期](agent-workflow/references/paths.md)
- [YAML 规范与命令](agent-workflow/references/yaml-schemas.md)
- [Forest 内容摘写指南](agent-workflow/references/content-guidance.md)

## 开发验证

```bash
python3 -B -m unittest discover -s tests -v
```

测试覆盖森林检查、上下文检索与缓存、状态并发、依赖与审计、多 Agent 协同、安装链接和更新安全。

## License

[MIT](LICENSE)
