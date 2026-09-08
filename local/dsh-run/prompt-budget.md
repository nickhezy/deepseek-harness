# Request envelope and prompt budget

English | [中文](prompt-budget.zh.md)

What every DSH agent pays before it has read a single word of its task, measured rather than estimated. The interface reference is [`README.md`](README.md); this file records the standing facts a benchmark needs to reason about cost.

Numbers below come from collection-mode traces of runs on 2026-09-08, composition `dsh-base` + `dsh-headless` + [`foreground.patch.yml`](foreground.patch.yml), route `openrouter/deepseek/deepseek-v4-flash`. Every figure is reproducible with the commands in [Re-measuring](#re-measuring); re-derive them after any change to the composed tool set, because they will move.

## The fixed envelope: ~6,700 tokens

`request/header` records the envelope exactly as the provider received it: `config`, `system`, `tools`. Nothing else in it varies with the task.

| Part | Chars | ~Tokens | Share |
|---|---:|---:|---:|
| System prompt | 3,679 | 895 | 13% |
| 23 tool schemas | 23,871 | 5,808 | 87% |
| **Envelope total** | **27,550** | **~6,703** | |

Character counts are compact JSON, the shape that goes on the wire. The conversion is 4.11 chars/token, derived by regressing measured envelope-plus-message characters against the provider's own reported prompt size across 16 sessions; individual sessions land between 4.08 and 4.15, so treat the token column as ±2%.

**Tool schemas are 87% of it.** The system prompt is not where the budget goes.

| Tool | Chars | ~Tokens |
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
| **Total** | **23,871** | **5,808** |

Three of those are unreachable under foreground delegation. `job_output`, `job_kill` and `job_list` are the generic Task surface, and with background delegation closed no subagent can register a job for them to collect — 354 tokens per request that nothing in a delegating run can use. `list_agents` (315) is in the same position. Dropping all four is a ~670-token cut, 10% of the envelope, at the cost of any *other* background work (a long `bash`) losing its collection surface.

## Host and subagent get the same envelope

Byte-identical, verified by hashing `[system, tools]` from `request/header` across all 16 sessions of four runs — one root and three children each:

```
fe98a202833f  session-f8263d60-…/session.jsonl   (depth 0)
fe98a202833f  d9553bd6-…/session.jsonl           (depth 1)
fe98a202833f  afcc1657-…/session.jsonl           (depth 1)
fe98a202833f  f9d32300-…/session.jsonl           (depth 1)
```

A spawned child receives **the full 23-tool surface**, including its own `subagent` and `subagent_fork` — within `maxDepth: 3` it can delegate further. It does not inherit the parent's conversation history; it does inherit the parent's entire capability surface. The only per-agent variation is the task message and the runtime-context snapshot below.

The direct consequence for cost modelling: one parent plus three children pays `4 × 6,703 ≈ 26,800` envelope tokens before any task text, however small the task. A large part of multi-agent's cost disadvantage against a single agent is this constant multiplying, not the work itself.

[`tool-subagent`](../../packages/subagent/tool-subagent/README.md)'s `toolFilter` narrows a child's global tool layer, which is the knob for measuring whether a narrower child surface changes success rate or cost. It is not an authority ceiling.

## The one place they differ

Every turn re-sends a runtime-context snapshot as an ordinary user message. It is not part of the envelope and it is not the task, but it is boilerplate the agent always pays:

| | Chars | ~Tokens |
|---|---:|---:|
| Root session | 479 | 117 |
| Delegated child | 851 | 207 |

Both carry the file policy and approval policy. A child additionally carries two paragraphs stating that approval prompts are disabled, that its permission scope was fixed at start and cannot be widened from inside the session, and that it should report a scope limitation upward rather than retry a denied operation. That is the whole host/subagent prompt difference: **90 tokens of delegation-specific framing, and nothing else.**

## What the foreground overlay changed

| | Tools | Tool chars | Envelope |
|---|---:|---:|---:|
| Stock `dsh-base` | 25 | 25,883 | ~7,214 tok |
| With `foreground.patch.yml` | 23 | 23,871 | ~6,703 tok |

A ~511-token saving per request, ~7%. Disabling `tool-subagent-control` removes two tools, `send_message` **and** `interrupt_agent` — both only address continuable children, which foreground delegation cannot produce. The system prompt also loses 88 characters: the `tool:subagent` section that tells the model to start independent continuable delegations and keep working while they run.

## Re-measuring

From a collection-mode trace directory (`$DSH_TRACE_ROOT/--<workspace>--/`). These are the exact commands behind the tables above.

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

`inputTokens + cacheReadTokens` is the provider's own prompt size and is the only authoritative number here; character counts and the 4.11 ratio exist to attribute that total to parts. A cached request reports most of the prompt under `cacheReadTokens` — the envelope is stable across requests, which is exactly what a prompt cache is for, so a run's second request is far cheaper than its first without the envelope having shrunk.
