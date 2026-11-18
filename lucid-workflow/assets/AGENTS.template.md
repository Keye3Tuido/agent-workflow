<!-- agent-workflow:start -->
# Lucid Workflow：要求、提问、任务与文档森林

保持用户目标、明确要求、实际证据和待办。用户指令及宿主高优先级规则优先；本区块不扩大授权。

## 位置与路径

| 位置 | 内容 |
| --- | --- |
| Agent 自有全局目录 | 宿主指导入口、Skill 链接和专属配置 |
| Agent 共享全局目录 | 权威 `AGENTS.md` 和 `skills/lucid-workflow/` |
| 项目根目录 | 项目 `AGENTS.md`、`.doc-forest.yml`、`.doc-forest/`、`.todos.yml`、`.agent-workflow-audit.jsonl` |
| 会话目录 | 当前会话的 `requirements.yml`、`questions.yml`、对应审计和运行状态 |

```text
macOS／Linux：
$HOME/.agents/AGENTS.md
$HOME/.agents/skills/lucid-workflow/
$HOME/.local/state/agent-workflow/<session-id>/requirements.yml
$HOME/.local/state/agent-workflow/<session-id>/questions.yml
$HOME/.local/state/agent-workflow/<session-id>/requirements-audit.jsonl

Windows：
%USERPROFILE%\.agents\AGENTS.md
%USERPROFILE%\.agents\skills\lucid-workflow\
%LOCALAPPDATA%\agent-workflow\<session-id>\requirements.yml
%LOCALAPPDATA%\agent-workflow\<session-id>\questions.yml
%LOCALAPPDATA%\agent-workflow\<session-id>\requirements-audit.jsonl

```

| 宿主 | 全局指导入口 | Skill 入口 |
| --- | --- | --- |
| Codex | `$HOME/.codex/AGENTS.md` | 直接读取共享 Skill |
| Claude Code | `$HOME/.claude/CLAUDE.md` | `$HOME/.claude/skills/lucid-workflow` → 共享 Skill |
| Cursor | Settings → Rules → User Rules；项目用 `AGENTS.md` | 共享 Skill 或 `$HOME/.cursor/skills/lucid-workflow` 链接 |
| Gemini CLI | `$HOME/.gemini/GEMINI.md` | 共享 Skill 或 `$HOME/.gemini/skills/lucid-workflow` 链接 |
| GitHub Copilot | 宿主指导设置；项目用 `AGENTS.md` | 直接读取共享 Skill |
| OpenCode | `$HOME/.config/opencode/AGENTS.md` | 直接读取共享 Skill |

共享目录是权威源，不代表宿主自动加载。按宿主支持的方式接入，Skill 只留一个实体。路径和文件生命周期见 `<共享Skill>/references/paths.md`。

## 会话开始检查更新

宿主加载本规则后、实质工作前，直接查询公开 API `https://api.github.com/repos/Keye3Tuido/lucid-workflow/releases/latest`，不调用 `gh`：

```bash
python3 <共享Skill>/scripts/update_skill.py check
```

`update_available: true` 时才执行 `apply`。更新器校验版本、资产地址和压缩包，保留旧目录备份；无更新或更新成功时不发送常规聊天提示，需要重新加载 Skill、复核模板或出现异常时明确说明。磁盘更新不视为当前会话已经加载。Skill 更新不自动覆盖共享 `AGENTS.md`，模板变化须按宿主规则复核接入。失败时使用当前版本，不重复请求。

## 默认 MCP 模式

新项目和明确迁移的项目默认使用 MCP，详细规则见 `<共享Skill>/references/mcp-service.md`。宿主尚未配置时应明确提示一次性接入，不得静默改用 CLI 写入。服务启动、保活、停止和更新由宿主或系统服务负责，Agent 不执行服务启动脚本。会话恢复时调用 `workflow.bootstrap`，正常协调使用 MCP 工具，答复或提交前调用 `workflow.checkpoint`。

既有 CLI 项目不自动迁移。服务不可用时不得自动改用 CLI 写入另一套状态；只读查询可降级，写入失败应报告。禁止自动合并两种模式的状态。

## 每轮维护要求

1. 收到消息后、实质处理前读取 `requirements.yml` 和 `questions.yml`。使用宿主会话目录或 `AGENT_WORKFLOW_SESSION_DIR`，核对会话 ID 和项目根目录。
2. 记录明确要求、禁止项、修改和取消内容。取消或到期即删除，ID 不复用；临时要求注明范围和失效条件。建议不得冒充用户要求。
3. 文件只存项目外的当前会话目录，并在会话中提供入口。没有可用目录时说明缺口，不得写入项目。
4. 答复前复核要求与实际行为；先修正，无法满足则说明要求、原因和缺口。只留本轮结果，不存隐藏思维、凭证或无关聊天。

## 不确定时先问

能通过源码、文档或工具核验的问题先自行核验。只有缺口会影响目标、权限、安全或结果正确性时才询问用户，不替用户猜测。一次只问一个关键问题，提供建议或明确选项；不受影响的工作继续执行。

以最少打扰为原则。用户已明确授权、验收或范围时，不重复确认，不要求二次确认；同一指令同时表达成果肯定和提交等操作授权且范围清楚时，可分别作为验收和授权依据，不拆成多轮，但不得从成果肯定推断额外授权。常规提交沿用原始授权时为零次额外确认，否则最多请求一次。能合并为一次关键决策的相关问题不拆问，已有 open 问题不重复提出。后台检查与维护成功时不额外打扰，任务结果和异常仍清楚说明。

提问前把问题、原因和必要上下文写入会话 `questions.yml`，并区分 `decision` 与 `context`。决策回答落实到要求、任务或操作后标记 applied；上下文回答作为可复用背景保留，可修订且不需要 applied。恢复时读取 open 和未落实的 decision，相关任务按需读取 context 回答。已落实或取消的记录最多保留 20 条，open、未落实 decision 和 context 回答不自动删除。

```bash
python3 <共享Skill>/scripts/session_questions.py ask --file <会话目录/questions.yml> --session-id <ID> --project-root <项目根目录> --kind <decision|context> --question <问题> --reason <需要用户决定的原因>
python3 <共享Skill>/scripts/session_questions.py answer --file <会话目录/questions.yml> --session-id <ID> --project-root <项目根目录> --id <Q-ID> --answer <回答>
python3 <共享Skill>/scripts/session_questions.py apply --file <会话目录/questions.yml> --session-id <ID> --project-root <项目根目录> --id <Q-ID>
python3 <共享Skill>/scripts/session_questions.py list --file <会话目录/questions.yml> --active
```

## 维护任务

1. 读取 `.todos.yml`，登记实际任务；无新任务不制造记录。
2. 状态只能 `todo → working → done → accepted`。开工前进入 `working`，验证后进入 `done`；用户肯定对应成果后即可进入 `accepted`，并记录对应原话或事实作为验收依据。用户未肯定或未回应时保留 `done`，不得自动验收，`clean-accepted` 不清理它。
3. 开工前所有直接依赖必须为 `done` 或 `accepted`；拒绝循环依赖。仍被依赖的任务不得普通删除，`clean-accepted` 可清理已验收任务及其已满足依赖。
4. 分别记录执行者与开始时间、完成时间与验证证据、验收时间与用户依据。阻塞原因独立记录。
5. 用户取消的任务直接删除。用户准许提交时，每次 `git commit` 前先把本次提交覆盖的 `done` 逐项转为 `accepted`，以用户准许提交的原话或事实作为验收依据，不得验收无关旧任务；再删除全部 `accepted`，校验 YAML 和森林。该操作不清空审计，审计保留或删除按 `<共享Skill>/references/paths.md` 的生命周期规则执行。
6. 旧 `done` 是待确认队列，不是历史档案。会话恢复时静默核对，提交前集中复核：有用户肯定或准许提交证据的补转 `accepted` 后清理；仍待确认或证据不足的保留；明确取消的删除，仍被依赖时先处理依赖关系。不得仅凭 Git 已提交、任务年龄或数量推断验收或删除。需要用户处理多项时，一次列出 ID、标题和完成时间，支持批量回答，不逐轮逐条追问；数量和时间只用于提醒。

状态写操作与审计共用同一文件锁，按 `prepared → committed` 或 `prepared → failed` 写入滚动 JSONL；超过 100 个完整操作时清理到最近 50 个，旧操作自动淘汰。纯读取和 `validate` 不写日志。任务审计位于项目根目录 `.agent-workflow-audit.jsonl`，要求审计位于会话目录 `requirements-audit.jsonl`。记录操作、执行者、会话、目标、状态迁移和成功时的状态 SHA-256；失败事件只写错误类型和脱敏类别，不记录原始异常文本、要求正文、源码或凭证。状态已替换但审计写入或压缩失败时，重试前核对状态与日志。

## 检查文档森林

仅在需要项目写入的对话自动检查：首次写入前一次，写入完成后一次；只读对话不自动检查。用户要求或任务需要时可额外执行。支持完整检查和 `--path` 局部检查。

```bash
python3 scripts/check_doc_forest.py --root <项目根目录>
python3 scripts/check_doc_forest.py --root <项目根目录> --path <相对路径> --format json
```

`write_check.excluded_paths` 中的维护写入不重复触发检查；`requirements.yml` 位于项目外，不加入该列表。脚本只读并报告结构问题，AI 负责语义维护，结构通过不等于描述准确。

## 按需求定位上下文

不要预读整棵森林。先从需求提取具体标识、文件名和领域词，再运行：

```bash
python3 scripts/forest_context.py --root <项目根目录> --task <需求> --keyword <关键词> --max-results 10
```

已知路径时添加 `--path <相对路径>`。定位器输出候选文件、核心成员、匹配原因、相关测试、相关模块和源码行号，不返回整份文件。检索范围包括现有职责、边界、入口、依赖目标和原因。嵌套文件继承最近模块根目录的元数据。自动聚焦按模块汇总关键词特异性和不同关键词覆盖，通用内容频率不得单独决定模块；证据接近时报告 `focus_ambiguous` 且不过滤，显式路径锚点优先。文件必须自身命中路径、专属说明、源码或路径锚点；目录摘要仅用于加权。相关测试按命名对应及同模块关键词证据补充。按输出定向读取源码并核验，关键词匹配不等于完整覆盖。派生 SQLite 缓存仅写 `.cache/agent-workflow/`，可删除重建，不进入 Forest，不修改源码或 Git 忽略规则；`--no-cache` 可禁用。

需要模块级架构时，按目录或文件路径查看局部直接关系。架构查询始终读取当前森林元数据，不复用上下文缓存：

```bash
python3 scripts/forest_architecture.py --root <项目根目录> --path <模块路径>
```

## 森林规则

- 每个纳入目录对应一个镜像目录，每层只有一个 `info.yml`，不得混入其他文件。
- `info.yml` 只写当前目录的 `purpose`、`contents_summary` 和有维护价值的文件说明。目录路径由位置推导，不重复保存。
- 目录或文件都可作为功能模块，按需添加 `boundary`、`entry`、`depends_on`。只记录直接架构依赖及原因，不记录模块内部调用、反向或传递依赖；`kind` 默认 `call`，可选 `config`、`data`、`event`。
- `directories` 完整列出有效直接子目录，但只写 `name`；子目录说明保存在其自身 `info.yml`，不在父级重复。
- 默认省略空列表和默认值。仅在需要停止递归时写 `recurse: false`：保留当前节点、停止镜像后代，且该节点不列下级目录。
- 森林深度随维护调整：模块边界稳定且任务需要时逐步展开；职责合并或子级说明失去独立价值时折叠。折叠保留当前节点摘要并清理后代镜像，展开补齐子级说明，不按理解程度或时间自动膨胀。
- `forest.allowlist` 非空时仅保留目标、后代和必要祖先；`forest.excluded_paths` 整棵排除且优先。依赖、Git 和构建目录按配置处理。
- 不跟随目录符号链接；局部检查须解析祖先规则，不得误报主动裁剪的后代。
- `requirements` 默认 `required + session`，`todos` 的空字段和空列表省略，状态字段按实际需要出现。
- 写 `info.yml` 前读 `references/content-guidance.md`；编辑 YAML 前读 `references/yaml-schemas.md`。

## 写入与 Git

- 所有 Agent 遵循同一规则，不区分主、子 Agent 权限。
- 共享 YAML 的读取、修改、写回全程加锁；同目录临时文件刷新后原子替换。原子替换不能替代锁。
- 不使用 Hook，不声称提示词能硬性保证触发。
- 项目待办初始化或写入时，默认把 `.agent-workflow-audit.jsonl` 和 `..todos.yml.lock` 加入项目根 `.gitignore`，保留其他条目；森林与上下文扫描默认排除工作流审计 JSONL 和状态锁。不主动把 `.doc-forest/`、`.doc-forest.yml`、`.todos.yml` 或其他现有文件加入 `.gitignore`、`.git/info/exclude` 等忽略规则；已有规则只报告，不擅改。不自动暂存或提交。
- 行动和答复均以实际证据为准，执行后核验真实结果。

## 以读者为中心的信息表达

适用于回复、文档、界面文字、代码注释和其他交付物：

1. **明确读者与用途**：选择必要的术语、背景和深度，让成品独立可用；制作经过仅在协作或溯源需要时保留。
2. **说明当前事实**：写清能力、条件、操作、结果和必要原因；否定、例外和对比须有助于理解或行动。
3. **保留有效信息**：删除重复、空话和无关背景；按结论、操作、条件、原因、细节组织，条件靠近操作，限制靠近能力。
4. **适配载体**：文档支持理解与执行，界面文字说明状态与动作，注释解释维护意图与约束，协作回复说明改动、验证和待办。
5. **保持语义完整**：保留范围、前提、例外、时间和确定程度，区分事实、推断、建议与待核验信息，不为肯定表达而虚构。
6. **以确认设计为准**：无用途的未发布旧设计默认移除；已发布设计存在兼容依赖时，破坏性变更前说明影响并取得确认。
7. **交付前静默复核**：检查信息是否明确可执行，是否仍有无关过程、重复或歧义，是否遗漏事实、条件、原因和边界。所有 Agent 均适用。
<!-- agent-workflow:end -->
