---
name: agent-workflow
description: Maintain session requirements, user questions, project todos, and .doc-forest summaries; locate task-relevant files and lines. Use for AGENTS.md rules, uncertainty questions, session recovery, project-write checks, context discovery, pre-commit cleanup, and one-command installation.
---

# Lucid Workflow

常驻 `AGENTS.md` 保存规则；本 Skill 提供操作。脚本检查结构和定位证据，AI 核验语义。不使用 Hook，不区分主、子 Agent 权限。

`assets/AGENTS.template.md` 是权威指导模板。源仓库任何目录都不保留可自动加载的 `AGENTS.md`，也不保存自身的 todos、审计或文档森林；只有应用工作流的目标项目才使用这些状态文件。文件持久化、维护和清理规则见 [paths.md](references/paths.md)。

## 会话开始检查更新

宿主加载规则后、实质工作前运行：

```bash
python3 <共享Skill>/scripts/update_skill.py check
```

固定查询 `https://github.com/Keye3Tuido/lucid-workflow/releases/latest`。仅 `update_available: true` 时执行 `apply`；更新器校验资产并保留旧目录备份。更新后重新加载 Skill。Skill 更新不自动覆盖共享 `AGENTS.md`，模板变化须按宿主规则复核接入。查询优先使用已登录的 `gh`，无 `gh` 时使用公开 API；失败时使用当前版本并报告，不重复请求。

## 恢复状态

1. 实质处理前读取会话目录或 `AGENT_WORKFLOW_SESSION_DIR` 中的 `requirements.yml` 和 `questions.yml`，核对 `session.id` 和 `session.project_root`。
2. 更新新增、修改、取消和到期的要求；建议经用户确认后才记录。
3. 读取 `.todos.yml`，登记并更新实际任务。
4. 读取 `questions.yml` 中的 open 问题；已有回答沿用原决定，不重复提问。
5. 答复前复核要求，保存本轮结果。

编辑 YAML 前读 [yaml-schemas.md](references/yaml-schemas.md)；写目录或文件说明前读 [content-guidance.md](references/content-guidance.md)。不得保存隐藏思维、凭证或无关聊天。

## 不确定时先问

能通过源码、文档或工具核验的问题先自行核验。只有缺口会影响目标、权限、安全或结果正确性时才询问用户；不要替用户猜测。一次只问一个关键问题，提供建议或明确选项，独立工作可继续进行。

提问前将问题、原因和必要上下文写入会话 `questions.yml`，用户回答后及时记录，取消的问题不再阻塞。恢复会话时先读 open 问题，已回答的问题不重复询问。

```bash
python3 scripts/session_questions.py init --file <会话目录/questions.yml> --session-id <ID> --project-root <项目根目录>
python3 scripts/session_questions.py ask --file <文件> --session-id <ID> --project-root <根目录> --question <问题> --reason <为什么需要用户决定>
python3 scripts/session_questions.py answer --file <文件> --session-id <ID> --project-root <根目录> --id <Q-ID> --answer <回答>
python3 scripts/session_questions.py list --file <文件> --status open
```

## 初始化

模板位于 `assets/`：`requirements.yml` 和 `questions.yml` 放会话目录，`todos.yml` 和 `doc-forest.yml` 放项目根目录并加前导点，`info.yml` 放各镜像目录。

```bash
python3 scripts/state_files.py requirements init --file <会话目录/requirements.yml> --session-id <ID> --project-root <项目根目录>
python3 scripts/state_files.py todos init --file <项目根目录/.todos.yml>
```

## 维护状态

状态修改必须经脚本加锁、校验和原子替换：

```bash
python3 scripts/state_files.py requirements upsert --file <文件> --session-id <ID> --project-root <根目录> --text <要求>
python3 scripts/state_files.py requirements review --file <文件> --session-id <ID> --project-root <根目录> --results <JSON数组>
python3 scripts/state_files.py todos add --file <文件> --title <任务> --acceptance-criteria <完成条件> [--dependency <TASK-ID>]
python3 scripts/state_files.py todos transition --file <文件> --id <TASK-ID> --to working --executor <执行者ID>
python3 scripts/state_files.py todos transition --file <文件> --id <TASK-ID> --to done --verification <验证结果>
python3 scripts/state_files.py todos transition --file <文件> --id <TASK-ID> --to accepted --acceptance-evidence <用户验收依据>
python3 scripts/state_files.py todos clean-accepted --file <文件>
```

默认要求为 `required + session`。禁止项、临时范围、任务 ID 和不同的原文引用按需添加。空字段和空列表省略。`requirements remove` 删除失效要求，`todos remove` 删除取消任务，`set-blocked`／`clear-blocked` 维护阻塞。修改后运行对应 `validate`。

任务开工前，所有直接依赖必须为 `done` 或 `accepted`。循环依赖会使校验失败；仍被其他任务依赖时不得普通删除。`clean-accepted` 可清理已验收任务及其已满足依赖。

状态写命令与审计共用同一文件锁：写入前追加 `prepared`，成功后追加带状态 SHA-256 的 `committed`，失败追加 `failed`。超过 100 个完整操作时清理到最近 50 个，旧操作自动淘汰；纯读取和 `validate` 不写日志。项目任务日志为项目根目录的 `.agent-workflow-audit.jsonl`，会话要求日志为会话目录的 `requirements-audit.jsonl`。日志记录操作、执行者、会话、目标和状态迁移；失败事件只写错误类型和脱敏类别，不记录原始异常文本、要求正文、源码或凭证。任务执行者可由 `AGENT_WORKFLOW_SESSION_ID` 标识。若状态已替换但审计写入或压缩失败，命令会报错，重试前须核对状态与日志。

## 定位任务上下文

不要预读整棵森林。先从需求提取具体标识、文件名和领域词：

```bash
python3 scripts/forest_context.py --root <项目根目录> --task <需求> --keyword <关键词> --max-results 10
```

可重复 `--keyword` 和 `--path`。输出候选文件、核心成员、相关测试、相关模块和行号；`--format json` 提供扫描统计。检索复用现有职责、边界、入口和依赖字段，嵌套文件继承最近模块根目录的元数据。自动聚焦按模块汇总关键词特异性和不同关键词覆盖，证据接近时报告 `focus_ambiguous` 且不过滤，显式路径锚点优先。目录摘要只加权，文件必须自身命中或位于显式路径锚点。相关测试按命名对应及同模块关键词证据补充。派生 SQLite 缓存仅写 `.cache/agent-workflow/`，可删除重建，可用 `--no-cache` 禁用；不进入 Forest，不修改源码或 Git 忽略规则。按输出定向读取源码和测试，关键词匹配不等于完整覆盖。

按目录或文件路径查看局部模块架构：

```bash
python3 scripts/forest_architecture.py --root <项目根目录> --path <模块路径>
```

架构查询始终读取当前森林元数据，不复用上下文缓存。输出职责边界、成员、直接依赖和查询时推导的反向依赖，不声称覆盖完整运行时调用链。

## 维护森林

项目写入前和完成后各检查一次；只读对话不自动检查：

```bash
python3 scripts/check_doc_forest.py --root <项目根目录>
python3 scripts/check_doc_forest.py --root <项目根目录> --path <相对路径> --format json
```

退出码：`0` 通过，`1` 有结构问题，`2` 配置或运行错误。根据报告补建或删除镜像，更新当前目录及父级条目，再定向验证，不循环检查。

`info.yml` 的目录路径由位置推导；子目录说明只存于其自身文件，父级只写 `name`。空列表和默认值省略，仅在停止递归时写 `recurse: false`。`.doc-forest` 永远排除；默认排除 `.git`、`node_modules`、`.venv`、`venv`，其他目录须明确配置。

## 提交前

确认 `accepted` 均有用户验收依据，运行 `todos clean-accepted`，再校验状态和森林。该命令只清理已验收任务，不清空审计；审计在每次写入后滚动压缩，保留或删除规则见 [paths.md](references/paths.md)。不修改 Git 忽略规则，不自动暂存或提交。

## 安装

先读 [paths.md](references/paths.md)。权威 Skill 默认位于 `$HOME/.agents/skills/agent-workflow/`，Windows 使用 `%USERPROFILE%\.agents\skills\agent-workflow\`。

解包后运行一次：

```bash
python3 scripts/install.py --host auto
```

安装器会安装共享 Skill、检测并链接宿主、合并 `assets/AGENTS.template.md` 的指导区块，保留已有其他规则。使用 `--host all` 安装全部支持的宿主；已有共享实体需要复用时添加 `--use-existing`。不支持目录链接时明确报告，不复制第二份。安装后重启或开启新会话，并核验宿主实际加载。`install_links.py` 仅用于需要分开管理链接的低层操作。
