---
name: agent-workflow
description: Maintain session requirements, project todos, and .doc-forest summaries; locate task-relevant files and lines. Use for AGENTS.md rules, session recovery, project-write checks, context discovery, summary writing, pre-commit cleanup, global paths, and shared Skill links.
---

# Agent workflow

常驻 `AGENTS.md` 保存规则；本 Skill 提供操作。脚本检查结构和定位证据，AI 核验语义。不使用 Hook，不区分主、子 Agent 权限。

## 恢复状态

1. 实质处理前读取会话目录或 `AGENT_WORKFLOW_SESSION_DIR` 中的 `requirements.yml`，核对 `session.id` 和 `session.project_root`。
2. 更新新增、修改、取消和到期的要求；建议经用户确认后才记录。
3. 读取 `.todos.yml`，登记并更新实际任务。
4. 答复前复核要求，保存本轮结果。

编辑 YAML 前读 [yaml-schemas.md](references/yaml-schemas.md)；写目录或文件说明前读 [content-guidance.md](references/content-guidance.md)。不得保存隐藏思维、凭证或无关聊天。

## 初始化

模板位于 `assets/`：`requirements.yml` 放会话目录，`todos.yml` 和 `doc-forest.yml` 放项目根目录并加前导点，`info.yml` 放各镜像目录。

```bash
python3 scripts/state_files.py requirements init --file <会话目录/requirements.yml> --session-id <ID> --project-root <项目根目录>
python3 scripts/state_files.py todos init --file <项目根目录/.todos.yml>
```

## 维护状态

状态修改必须经脚本加锁、校验和原子替换：

```bash
python3 scripts/state_files.py requirements upsert --file <文件> --session-id <ID> --project-root <根目录> --text <要求>
python3 scripts/state_files.py requirements review --file <文件> --session-id <ID> --project-root <根目录> --results <JSON数组>
python3 scripts/state_files.py todos add --file <文件> --title <任务> --acceptance-criteria <完成条件>
python3 scripts/state_files.py todos transition --file <文件> --id <TASK-ID> --to working --executor <执行者ID>
python3 scripts/state_files.py todos transition --file <文件> --id <TASK-ID> --to done --verification <验证结果>
python3 scripts/state_files.py todos transition --file <文件> --id <TASK-ID> --to accepted --acceptance-evidence <用户验收依据>
python3 scripts/state_files.py todos clean-accepted --file <文件>
```

默认要求为 `required + session`。禁止项、临时范围、任务 ID 和不同的原文引用按需添加。空字段和空列表省略。`requirements remove` 删除失效要求，`todos remove` 删除取消任务，`set-blocked`／`clear-blocked` 维护阻塞。修改后运行对应 `validate`。

## 定位任务上下文

不要预读整棵森林。先从需求提取具体标识、文件名和领域词：

```bash
python3 scripts/forest_context.py --root <项目根目录> --task <需求> --keyword <关键词> --max-results 10
```

可重复 `--keyword` 和 `--path`。输出候选文件、核心成员、相关测试、相关模块和行号；`--format json` 提供扫描统计。检索复用现有职责、边界、入口和依赖字段，嵌套文件继承最近模块根目录的元数据。目录摘要只加权，文件必须自身命中或位于显式路径锚点。相关测试按命名对应及同模块关键词证据补充。派生 SQLite 缓存仅写 `.cache/agent-workflow/`，可删除重建，可用 `--no-cache` 禁用；不进入 Forest，不修改源码或 Git 忽略规则。按输出定向读取源码和测试，关键词匹配不等于完整覆盖。

按目录或文件路径查看局部模块架构：

```bash
python3 scripts/forest_architecture.py --root <项目根目录> --path <模块路径>
```

输出职责边界、成员、直接依赖和查询时推导的反向依赖，不声称覆盖完整运行时调用链。

## 维护森林

项目写入前和完成后各检查一次；只读对话不自动检查：

```bash
python3 scripts/check_doc_forest.py --root <项目根目录>
python3 scripts/check_doc_forest.py --root <项目根目录> --path <相对路径> --format json
```

退出码：`0` 通过，`1` 有结构问题，`2` 配置或运行错误。根据报告补建或删除镜像，更新当前目录及父级条目，再定向验证，不循环检查。

`info.yml` 的目录路径由位置推导；子目录说明只存于其自身文件，父级只写 `name`。空列表和默认值省略，仅在停止递归时写 `recurse: false`。`.doc-forest` 永远排除；默认排除 `.git`、`node_modules`、`.venv`、`venv`，其他目录须明确配置。

## 提交前

确认 `accepted` 均有用户验收依据，运行 `todos clean-accepted`，再校验状态和森林。不修改 Git 忽略规则，不自动暂存或提交。

## 安装

先读 [paths.md](references/paths.md)。权威 Skill 默认位于 `$HOME/.agents/skills/agent-workflow/`；本机为 `/Users/user01/.agents/skills/agent-workflow/`。

```bash
python3 scripts/install_links.py --shared-root ~/.agents --host-skill-dir ~/.claude/skills
```

重复 `--host-skill-dir` 接入多个宿主。已有实体默认不覆盖，确认复用时加 `--use-existing`。不支持目录链接时明确报告，不复制第二份。`AGENTS.md` 仍须接入宿主实际指导入口。
