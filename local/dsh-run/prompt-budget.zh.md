# 请求信封与提示词预算

[English](prompt-budget.md) | 中文

每个 DSH 智能体在读到任务的第一个字之前就已经付出的代价——实测，不是估算。接口参考见 [`README.md`](README.md)；本文件记录的是 benchmark 在推算成本时需要的那些常量事实。

下列数字来自 2026-09-08 若干次运行的采集模式轨迹，组合为 `dsh-base` + `dsh-headless` + [`foreground.patch.yml`](foreground.patch.yml)，路由为 `openrouter/deepseek/deepseek-v4-flash`。每个数字都可以用[重新测量](#重新测量)里的命令复现；合成后的工具集一旦变动就要重新推导，因为它们一定会变。

## 固定信封：约 6,700 token

`request/header` 原样记录了 provider 收到的信封：`config`、`system`、`tools`。里面没有任何东西随任务变化。

| 部分 | 字符 | ≈token | 占比 |
|---|---:|---:|---:|
| 系统提示词 | 3,679 | 895 | 13% |
| 23 个工具 schema | 23,871 | 5,808 | 87% |
| **信封合计** | **27,550** | **~6,703** | |

字符数按紧凑 JSON 计，也就是真正上线的形态。换算比例 4.11 字符/token，是用 16 个 session 的"信封加消息字符数"对 provider 自报的 prompt 大小回归得到的；单个 session 落在 4.08 到 4.15 之间，所以 token 那一列请按 ±2% 看待。

**工具 schema 占了 87%。** 预算不是花在系统提示词上的。

| 工具 | 字符 | ≈token |
|---|---:|---:|
| `workflow` | 3,986 | 970 |
| `bash` | 3,242 | 789 |
| `str_replace_editor` | 2,380 | 579 |
| `todo_write` | 1,344 | 327 |
| `list_agents` | 1,296 | 315 |
| `update_goal` | 1,141 | 278 |
| `edit` | 1,072 | 261 |
| `glob` | 922 | 224 |
| `subagent_fork` | 865 | 210 |
| `subagent` | 832 | 202 |
| `job_output` | 832 | 202 |
| `ralph` | 826 | 201 |
| `write` | 775 | 189 |
| `grep` | 765 | 186 |
| `create_goal` | 697 | 170 |
| `exit_plan_mode` | 549 | 134 |
| `job_kill` | 457 | 111 |
| `read` | 438 | 107 |
| `skill` | 376 | 91 |
| `read_image` | 323 | 79 |
| `get_goal` | 319 | 78 |
| `web_search` | 266 | 65 |
| `job_list` | 168 | 41 |
| **合计** | **23,871** | **5,808** |

其中三个在前台委派下根本够不着。`job_output`、`job_kill`、`job_list` 是通用 Task 界面，而后台委派既已关闭，就没有任何子智能体能注册出让它们去回收的 job——每次请求 354 个 token，在一次委派运行里没有任何东西用得上。`list_agents`（315）处境相同。四个一起去掉能省约 670 token，占信封的 10%，代价是*其它*后台工作（比如一次长时间的 `bash`）失去回收界面。

## host 和 subagent 拿到的信封相同

逐字节相同：对四次运行、共 16 个 session（每次一根三子）的 `request/header` 取 `[system, tools]` 做哈希验证过：

```
fe98a202833f  session-f8263d60-…/session.jsonl   (depth 0)
fe98a202833f  d9553bd6-…/session.jsonl           (depth 1)
fe98a202833f  afcc1657-…/session.jsonl           (depth 1)
fe98a202833f  f9d32300-…/session.jsonl           (depth 1)
```

spawn 出来的子智能体拿到的是**完整的 23 工具表面**，包括它自己的 `subagent` 和 `subagent_fork`——在 `maxDepth: 3` 之内它还能继续往下委派。它不继承父的对话历史，但继承父的整套能力表面。逐个智能体的差异只有任务消息，以及下面那份运行时上下文快照。

对成本建模的直接后果：一父三子的一次运行，在任何任务文本之前就要付 `4 × 6,703 ≈ 26,800` 个信封 token，任务再小也一样。多智能体相对单智能体的成本劣势，很大一部分是这个常数在乘，而不是工作本身。

[`tool-subagent`](../../packages/subagent/tool-subagent/README.md) 的 `toolFilter` 可以收窄子智能体的全局工具层，这正是"更窄的子表面是否影响成功率或成本"的可测旋钮。它不是权限天花板。

## 唯一的差别所在

每个 turn 都会把一份运行时上下文快照当作普通用户消息重发一遍。它不属于信封，也不是任务，但确实是智能体每次都要付的样板：

| | 字符 | ≈token |
|---|---:|---:|
| 根 session | 479 | 117 |
| 被委派的子 session | 851 | 207 |

两者都带着文件策略和审批策略。子 session 额外带两段话，声明审批提示已禁用、它的权限范围在启动时就已固定且无法从会话内部放宽、以及遇到范围限制时应当向上汇报而不是重试被拒的操作。这就是 host 与 subagent 提示词的全部差别：**90 个 token 的委派专用说明，此外别无二致。**

## 前台覆盖层改变了什么

| | 工具数 | 工具字符 | 信封 |
|---|---:|---:|---:|
| 标准 `dsh-base` | 25 | 25,883 | ~7,214 tok |
| 加 `foreground.patch.yml` | 23 | 23,871 | ~6,703 tok |

每次请求省约 511 token，约 7%。禁用 `tool-subagent-control` 移除了两个工具：`send_message` **和** `interrupt_agent`——两者都只面向可续子智能体，而前台委派产生不出这种子智能体。系统提示词也少了 88 个字符：那段 `tool:subagent` 小节，原本用来告诉模型把互相独立的可续委派一起发起、并在它们运行期间继续干活。

## 重新测量

在采集模式的轨迹目录下执行（`$DSH_TRACE_ROOT/--<workspace>--/`）。上面那些表格背后就是这几条命令。

```sh
# envelope of one session
jq -r 'select(.type=="request/header") | .data.header
       | "system \(.system|length) ch, \(.tools|length) tools, \([.tools[]|tojson]|join("")|length) ch"' \
  <session>/session.jsonl | head -1

# what the provider actually charged for the first request
jq -r 'select(.type=="assistant/message" and .data.usage) | .data.usage
       | "prompt = \(.inputTokens) uncached + \(.cacheReadTokens) cached"' \
  <session>/session.jsonl | head -1

# per-tool schema cost, largest first
jq -r 'select(.type=="request/header") | .data.header.tools[] | "\(tojson|length)\t\(.name)"' \
  <session>/session.jsonl | sort -rn

# is every session in the run carrying the same envelope?
for f in */session.jsonl; do
  printf '%s  %s\n' "$(jq -c 'select(.type=="request/header") | [.data.header.system, .data.header.tools]' \
    "$f" | head -1 | shasum | cut -c1-12)" "$f"
done | sort
```

`inputTokens + cacheReadTokens` 是 provider 自报的 prompt 大小，也是这里唯一权威的数字；字符数和 4.11 这个比例的存在，只是为了把那个总数归因到各个部分上。命中缓存的请求会把大部分 prompt 记在 `cacheReadTokens` 下——信封在多次请求之间是稳定的，而这正是提示词缓存的用途所在，所以一次运行的第二个请求会比第一个便宜得多，并不意味着信封变小了。
