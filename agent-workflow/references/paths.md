# 全局、项目与会话路径

## 路径规则

- macOS／Linux 用 `$HOME`；Windows 用 `%USERPROFILE%` 和 `%LOCALAPPDATA%`。
- `<session-id>` 是当前会话的唯一标识，不得复用。
- 项目根目录是 Git 根目录或用户指定的工作区，不属于全局目录。

## 共享权威路径

| 内容 | macOS／Linux | Windows |
| --- | --- | --- |
| 指导源 | `$HOME/.agents/AGENTS.md` | `%USERPROFILE%\.agents\AGENTS.md` |
| Skill | `$HOME/.agents/skills/agent-workflow/` | `%USERPROFILE%\.agents\skills\agent-workflow\` |
| 会话根目录 | `$HOME/.local/state/agent-workflow/` | `%LOCALAPPDATA%\agent-workflow\` |

共享路径是权威源，不代表宿主自动读取其中的 `AGENTS.md`。

## 宿主入口

`~` 表示当前账户主目录；写命令时先展开为实际绝对路径。

| 宿主 | 全局指导入口 | Skill 发现或链接位置 |
| --- | --- | --- |
| Codex | `~/.codex/AGENTS.md` | `~/.agents/skills/agent-workflow/` |
| Claude Code | `~/.claude/CLAUDE.md` | `~/.claude/skills/agent-workflow` → 共享 Skill |
| Cursor | Settings → Rules → User Rules；项目用 `AGENTS.md` | 共享 Skill，或 `~/.cursor/skills/agent-workflow` 链接 |
| Gemini CLI | `~/.gemini/GEMINI.md` | 共享 Skill，或 `~/.gemini/skills/agent-workflow` 链接 |
| GitHub Copilot | 宿主指导设置；项目用 `AGENTS.md` | `~/.agents/skills/agent-workflow/` |
| OpenCode | `~/.config/opencode/AGENTS.md` | `~/.agents/skills/agent-workflow/` |

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
python3 /absolute/unpacked/agent-workflow/scripts/install_links.py \
  --source /absolute/unpacked/agent-workflow \
  --shared-root "$HOME/.agents" \
  --host-skill-dir "$HOME/.claude/skills"
```

结果：

```text
实体：$HOME/.agents/skills/agent-workflow/
链接：$HOME/.claude/skills/agent-workflow -> $HOME/.agents/skills/agent-workflow/
```

已有实体默认不覆盖，确认复用时加 `--use-existing`。链接安装不等于指导文件已加载，`AGENTS.md` 仍须接入宿主指导入口。
