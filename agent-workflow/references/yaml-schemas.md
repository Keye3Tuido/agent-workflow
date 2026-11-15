# YAML 规范与命令

## 目录

1. 通用规则
2. `.doc-forest.yml`
3. `info.yml`
4. `requirements.yml`
5. `.todos.yml`
6. 状态脚本命令

## 1. 通用规则

- UTF-8、LF、两空格缩进，不用 Tab；`schema_version` 为字符串 `"1.0"`。
- 禁止重复键、未知字段、锚点、别名、合并键和自定义标签。
- 时间使用带时区的 RFC 3339 字符串；脚本写入 UTC。
- ID 唯一且不复用，编号由 `next_requirement_number` 或 `next_task_number` 保存。
- 项目相对路径用 POSIX `/`，不得为绝对路径或包含空段、`.`、`..`、反斜杠。
- 默认省略空列表、空字符串、`null` 和默认枚举值；同一事实只在一个位置维护。

## 2. `.doc-forest.yml`

```yaml
schema_version: "1.0"
write_check:
  excluded_paths:
    - ".doc-forest"
    - ".todos.yml"
forest:
  allowlist: []
  excluded_paths:
    - ".git"
    - "node_modules"
    - ".venv"
    - "venv"
```

- `write_check.excluded_paths`：维护写入不重复触发检查，不改变镜像范围。`.doc-forest.yml` 不默认豁免；项目外的 `requirements.yml` 不加入。
- `forest.allowlist`：仅目录相对路径。空列表不限制；非空时仅镜像目标、后代和必要祖先。
- `forest.excluded_paths`：无 `/` 的条目匹配任意深度同名目录；含 `/` 的条目匹配指定目录。匹配目录及后代不镜像，优先于白名单。
- `.doc-forest` 永远排除。其他依赖和构建目录须明确配置，不自动推断。

## 3. `info.yml`

```yaml
schema_version: "1.0"
purpose: "当前目录职责"
contents_summary: "当前层内容作用"
boundary: "可选职责边界"
entry: "可选主要入口"
files:
  - name: "main.py"
    purpose: "文件模块职责"
    details: "可选关键元信息"
    boundary: "可选文件模块边界"
    entry: "main"
    core: true
    depends_on:
      - target: "src/helper.py"
        kind: "call"
        reason: "复用规则"
directories:
  - name: "module"
  - name: "generated"
    recurse: false
```

- `schema_version`、`purpose`、`contents_summary` 必需；`files`、`directories` 可省略，默认 `[]`。
- 目录路径由 `info.yml` 的位置推导，不设置 `directory` 或 `path`。
- 目录和文件都可作为功能模块，按需添加 `boundary`、`entry`、`depends_on`；普通目录和文件无需添加。
- `boundary` 说明不负责的事项或调用约束，`entry` 仅记录主要入口，均须为非空字符串。
- `depends_on` 仅记录直接架构依赖，每项必须有 `target`、`reason`，`kind` 默认 `call`，可选 `config`、`data`、`event`。不得保存反向或传递依赖。
- `target` 必须指向项目内真实文件或目录。无 `/` 时相对当前目录解析，含 `/` 时相对项目根目录解析；目标加类型不得重复。
- 文件条目必须有 `name`、`purpose`，`details` 可选；`core: true` 仅标记架构主编排类或稳定契约，默认省略。名称是当前层直接文件，条目必须存在。
- 目录条目必须有 `name`，完整列出纳入范围的直接子目录。说明只保存在该子目录自己的 `info.yml`，父级不重复 `purpose` 或 `contents_summary`。
- `recurse` 仅在为 `false` 时写出：保留当前节点、停止镜像后代，该节点不列下级目录。默认 `true`，不得显式写出。
- 两组条目按 `name` 的 Unicode 码点排序且不得重复，名称不得含路径分隔符。
- 被排除或不在白名单的目录不列入。脚本不跟随目录符号链接；森林仅允许真实目录和各层一个 `info.yml`。

语义写法见 [content-guidance.md](content-guidance.md)。

## 4. `requirements.yml`

```yaml
schema_version: "1.0"
session:
  id: "SESSION_ID"
  project_root: "/absolute/project/root"
  created_at: "2026-11-14T00:00:00Z"
  updated_at: "2026-11-14T00:00:00Z"
next_requirement_number: 2
requirements:
  - id: "REQ-001"
    text: "用户明确要求"
response_review:
  reviewed_at: "2026-11-14T00:00:00Z"
  results:
    - requirement_id: "REQ-001"
      status: "met"
      evidence: "满足要求的事实或位置"
```

每条要求仅需 `id`、`text`。按需添加：

- `polarity`：默认 `required`，禁止项写 `forbidden`。
- `scope`：默认 `session`，可写 `phase`、`task`、`turn`。
- `expires_when`：非 session 范围必填。
- `task_ids`：仅 task 范围使用，至少一项。
- `source_quote`：仅当 `text` 不能完整代表用户原文时填写；相同则省略。

文件须位于项目外；修改时核对会话 ID 和项目根目录。取消或到期即删除，ID 不复用。

`response_review.results` 必需，`reviewed_at` 在有结果时必需。复核须恰好覆盖全部有效要求；`status` 为 `met`、`unmet`、`not_applicable`。`evidence` 必填；`reason` 仅在 `unmet` 时必填，其他状态省略。无复核时写 `response_review: {results: []}`。

## 5. `.todos.yml`

```yaml
schema_version: "1.0"
updated_at: "2026-11-14T00:00:00Z"
next_task_number: 2
tasks:
  - id: "TASK-001"
    title: "任务名称"
    acceptance_criteria:
      - "可检查的完成条件"
    status: "working"
    created_at: "2026-11-14T00:00:00Z"
    updated_at: "2026-11-14T00:00:00Z"
    executor: "agent-session-id"
    started_at: "2026-11-14T00:00:00Z"
```

每项仅需 `id`、`title`、`acceptance_criteria`、`status`、`created_at`、`updated_at`。按需添加：

- `dependencies`：引用其他已有任务，不得重复、依赖自身或形成循环；所有直接依赖为 `done` 或 `accepted` 后才能开工。
- `working`：必须有 `executor`、`started_at`；同一执行者最多一个。
- `done`：还须有 `completed_at` 和非空 `verification`。
- `accepted`：还须有 `accepted_at` 和用户明确的 `acceptance_evidence`。
- `blocked_reason`：仅在存在阻塞时填写，解除时删除。

状态只能 `todo → working → done → accepted`。不属于当前状态的字段和空列表省略。用户取消的任务直接删除，但仍被依赖的任务须先解除依赖；每次 `git commit` 前用 `clean-accepted` 删除全部 `accepted` 及其已满足依赖。

## 6. 状态脚本命令

修改命令在同一文件锁内读取、校验、修改、再次校验、原子替换并追加审计；校验命令只读且不写审计。审计按 `prepared → committed` 或 `prepared → failed` 记录，成功事件包含状态文件 SHA-256。任务日志位于项目根目录 `.agent-workflow-audit.jsonl`，要求日志位于会话目录 `requirements-audit.jsonl`。失败事件只写错误类型和脱敏类别，日志不保存原始异常文本、要求正文、源码或凭证；若状态已替换但成功事件追加失败，重试前须核对状态与日志。

### 要求文件

```bash
python3 scripts/state_files.py requirements init --file <文件> --session-id <ID> --project-root <项目根目录>
python3 scripts/state_files.py requirements upsert --file <文件> --session-id <ID> --project-root <项目根目录> --text <要求>
python3 scripts/state_files.py requirements upsert --file <文件> --session-id <ID> --project-root <项目根目录> --id REQ-001 --text <修改后的要求>
python3 scripts/state_files.py requirements remove --file <文件> --session-id <ID> --project-root <项目根目录> --id REQ-001
python3 scripts/state_files.py requirements review --file <文件> --session-id <ID> --project-root <项目根目录> --results <JSON数组>
python3 scripts/state_files.py requirements validate --file <文件>
```

可选参数：`--polarity`、`--scope`、`--source-quote`、`--expires-when`；`--task-id` 可重复。

### 任务文件

```bash
python3 scripts/state_files.py todos init --file <文件>
python3 scripts/state_files.py todos add --file <文件> --title <任务> --acceptance-criteria <条件> [--acceptance-criteria <条件>] [--dependency <TASK-ID>]
python3 scripts/state_files.py todos remove --file <文件> --id TASK-001
python3 scripts/state_files.py todos transition --file <文件> --id TASK-001 --to working --executor <执行者ID>
python3 scripts/state_files.py todos transition --file <文件> --id TASK-001 --to done --verification <证据> [--verification <证据>]
python3 scripts/state_files.py todos transition --file <文件> --id TASK-001 --to accepted --acceptance-evidence <用户验收依据>
python3 scripts/state_files.py todos set-blocked --file <文件> --id TASK-001 --reason <原因>
python3 scripts/state_files.py todos clear-blocked --file <文件> --id TASK-001
python3 scripts/state_files.py todos clean-accepted --file <文件>
python3 scripts/state_files.py todos validate --file <文件>
```

失败时不替换原文件。成功返回 `0`，校验或状态问题返回 `1`，参数或运行错误返回 `2`。
