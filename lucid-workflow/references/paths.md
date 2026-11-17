# 全局、项目与会话路径

## 路径规则

- macOS／Linux 用 `$HOME`；Windows 用 `%USERPROFILE%` 和 `%LOCALAPPDATA%`。
- `<session-id>` 是当前会话的唯一标识，不得复用。
- 项目根目录是 Git 根目录或用户指定的工作区，不属于全局目录。

## 共享权威路径

| 内容 | macOS／Linux | Windows |
| --- | --- | --- |
| 指导源 | `$HOME/.agents/AGENTS.md` | `%USERPROFILE%\.agents\AGENTS.md` |
| Skill | `$HOME/.agents/skills/lucid-workflow/` | `%USERPROFILE%\.agents\skills\lucid-workflow\` |
| 会话根目录 | `$HOME/.local/state/agent-workflow/` | `%LOCALAPPDATA%\agent-workflow\` |

共享路径是权威源，不代表宿主自动读取其中的 `AGENTS.md`。

## 宿主入口

`~` 表示当前账户主目录；写命令时先展开为实际绝对路径。

| 宿主 | 全局指导入口 | Skill 发现或链接位置 |
| --- | --- | --- |
| Codex | `~/.codex/AGENTS.md` | `~/.agents/skills/lucid-workflow/` |
| Claude Code | `~/.claude/CLAUDE.md` | `~/.claude/skills/lucid-workflow` → 共享 Skill |
| Cursor | Settings → Rules → User Rules；项目用 `AGENTS.md` | 共享 Skill，或 `~/.cursor/skills/lucid-workflow` 链接 |
| Gemini CLI | `~/.gemini/GEMINI.md` | 共享 Skill，或 `~/.gemini/skills/lucid-workflow` 链接 |
| GitHub Copilot | 宿主指导设置；项目用 `AGENTS.md` | `~/.agents/skills/lucid-workflow/` |
| OpenCode | `~/.config/opencode/AGENTS.md` | `~/.agents/skills/lucid-workflow/` |

路径存在不代表已经加载；以宿主实际支持的方式为准。

## 会话目录

优先使用宿主的可恢复会话目录。否则把 `AGENT_WORKFLOW_SESSION_DIR` 设为当前会话专属目录：

```text
macOS／Linux：$HOME/.local/state/agent-workflow/<session-id>/
Windows：%LOCALAPPDATA%\agent-workflow\<session-id>\
```

`requirements.yml` 必须位于项目外且不被其他会话使用，文件中的会话 ID 和项目根目录须与当前工作一致，并在会话中提供入口。没有可用目录时报告缺口，不写入项目。

## 链接安装

权威 Skill 只保留一个实体。宿主位置使用目录链接，不使用 `.lnk`、路径文本或副本。

```bash
python3 /absolute/unpacked/lucid-workflow/scripts/install_links.py \
  --source /absolute/unpacked/lucid-workflow \
  --shared-root "$HOME/.agents" \
  --host-skill-dir "$HOME/.claude/skills"
```

结果：

```text
实体：$HOME/.agents/skills/lucid-workflow/
链接：$HOME/.claude/skills/lucid-workflow -> $HOME/.agents/skills/lucid-workflow/
```

已有实体默认不覆盖，确认复用时加 `--use-existing`。链接安装不等于指导文件已加载，`AGENTS.md` 仍须接入宿主指导入口。


## 文件持久化、维护与清理

### 共享安装与模板

| 文件或目录 | 持久化与维护 | 清理与更新规则 |
| --- | --- | --- |
| `lucid-workflow/assets/AGENTS.template.md` | 随 Skill 包保存的权威指导模板，不参与源仓库自动加载 | 安装时按宿主规则接入为 `AGENTS.md`；源仓库内不得改成自动加载文件名 |
| 共享目录的 `AGENTS.md` | 持久化共享指导源，按宿主方式接入 | Skill 更新不自动覆盖；模板变化时复核并明确更新，保留已有其他规则；内容不变时不改写或备份，真实变化产生的备份由用户确认后清理 |
| `skills/lucid-workflow/` | 持久化共享 Skill 实体，版本更新前检查并备份旧目录 | 更新后保留备份；确认新版本可用后由用户清理旧备份 |

### 目标项目

| 文件或目录 | 持久化与维护 | 清理规则 |
| --- | --- | --- |
| `.todos.yml` | 使用共享任务时保留，保存任务状态和下一编号；所有写入经状态脚本加锁、校验和原子替换 | `clean-accepted` 只删除已验收条目及其已满足依赖，不删除文件或编号；旧 `done` 在恢复和提交前集中核对，证据不足的保留；准许提交时先验收本次提交覆盖的 `done`，取消的任务按命令删除 |
| `.agent-workflow-audit.jsonl` | 位于项目根目录的项目协调审计；超过 100 个完整操作时清理到最近 50 个；待办写入时自动加入项目根 `.gitignore`，森林与上下文扫描默认排除 | 每次成功或失败后检查水位并自动淘汰旧操作；`git commit` 和 `clean-accepted` 不额外清空。不再需要时由用户明确删除；后续状态写入会重新生成 |
| 会话目录的 `requirements.yml` | 位于项目外，保存当前会话有效要求；每轮核对并删除取消或到期条目 | 会话结束且不再需要恢复时删除，不进入项目或提交 |
| 会话目录的 `questions.yml` | 位于项目外，保存问题、必要上下文、回答、类型和落实状态 | open、未落实 decision 和 context 回答不自动删除；已落实 decision 与 cancelled 合计最多保留 20 条。会话结束且不再需要恢复时删除 |
| 会话目录的 `requirements-audit.jsonl` | 位于项目外；超过 100 个要求操作时清理到最近 50 个 | 每次成功或失败后检查水位并自动淘汰旧操作；会话结束且不再需要追溯时与要求文件一并删除 |
| `.doc-forest.yml`、`.doc-forest/` | 启用森林的目标项目保留，保存导航配置和人工维护的摘要；项目写入前后检查并更新 | 它们不是缓存。停用森林时明确删除；只清理缓存不会清理森林 |
| `.cache/agent-workflow/` | 派生 SQLite 缓存，可按需重建，不作为事实来源 | 可随时删除，不纳入提交；再次检索时自动重建 |
| 状态文件旁的 `*.lock` | 写入期间的稳定锁文件，不保存业务事实；项目待办锁 `..todos.yml.lock` 自动加入项目根 `.gitignore`，会话锁位于项目外 | 森林与上下文扫描默认排除工作流锁；确认没有读写进程后可删除，不得在写入期间删除 |
| MCP 服务状态目录 | 仅 MCP 模式使用；保存项目外 SQLite、会话要求、Forest 缓存、单实例锁、端点和服务日志；不进入项目或 Git | SQLite 和会话状态需备份并显式迁移；缓存可删除重建；锁在运行中不得删除；失效端点由服务重建；日志在停止后按需清理 |
| `dist/*.skill` | 从 Skill 源码生成的分发资产，发布前重新构建并验证 | 可删除后重新生成，不属于协调状态 |
| 临时目录、`__pycache__`、`*.pyc`、`.DS_Store` | 测试或运行产生的临时内容 | 不纳入提交，确认无使用后删除 |

### Lucid Workflow 源仓库自身

源仓库只保存产品、测试、项目说明和发布资产：`lucid-workflow/`、`tests/`、`README.md`、`LICENSE` 和 `dist/`。权威指导模板是 `lucid-workflow/assets/AGENTS.template.md`，仓库内任何位置都不得保留会自动加载的 `AGENTS.md`。

源仓库自身不创建或提交 `.todos.yml`、`.agent-workflow-audit.jsonl`、`.doc-forest.yml`、`.doc-forest/` 或任何目录的 `AGENTS.md`，不得对本仓库运行工作流初始化或状态写入。检查和协同测试使用临时项目夹具，不对源仓库生成这些文件。
