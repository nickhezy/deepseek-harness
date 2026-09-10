# `dsh-run/` — submitting, watching, and reading multi-agent runs

English | [中文](README.zh.md)

`dsh-run.sh` is the whole interface: it boots DeepSeek Harness in one of a few named modes and reads back what a run did. The modes differ only in which `--patch` overlays reach the composed config tree and which profile boots, so nothing here edits a shared file and no mode leaves state behind for the next launch.

[`prompt-budget.md`](prompt-budget.md) records what an agent pays before it reads its task — the measured envelope, host versus subagent, and how to re-measure.

Two overlays do the work. [`foreground.patch.yml`](foreground.patch.yml) makes delegation synchronous — a subagent runs to completion inside its parent's tool call. [`trace.patch.yml`](trace.patch.yml) makes the session log readable — one JSON object per line instead of packed rows inside Zstandard frames. [`dsh-trace.py`](dsh-trace.py) reads the result, live or afterwards.

## What this fork changes

Everything below lives under `local/`; no upstream file is modified. Two things are changed from stock behavior, and each is set in two places — a per-launch overlay this tooling applies, and optionally a machine-level layer that also catches a raw `dsh` invocation.

| What changes | Parameter | Configured in |
|---|---|---|
| Delegation is synchronous, never background | `tool-subagent.enableRunInBackground: false`, same on `tool-subagent-fork`, and `tool-subagent-control.disabled: true` | [`foreground.patch.yml`](foreground.patch.yml), applied by every booting mode — and, for raw `dsh` too, the same rows copied into `$DSH_HOME/cordis.patch.yml` |
| Session logs are one JSON object per line | `session-persistence-jsonl.root` / `.compression: none` / `.packChunks: false` | [`trace.patch.yml`](trace.patch.yml), applied by `trace`, `batch` and `dump` |

Deployment settings are not overlays and live in the harness home:

| What | Parameter | Configured in |
|---|---|---|
| Model and provider route for every agent | `agent-default-model.provider` / `.model`, `llm-pi-ai.providers.<route>.apiKeyEnv` | `$DSH_HOME/settings.yaml` |
| The provider credential itself | `OPENROUTER_API_KEY` | `$DSH_HOME/.env`, mode `600` (or any earlier source in the credential order above) |
| Where collection-mode logs are written | `DSH_TRACE_ROOT` | environment; default `~/data/dsh-traces` |
| How `dsh` is invoked | `DSH_BIN`, and `TSX_TSCONFIG_PATH` which `dsh-run.sh` sets for source execution | environment |

Added tooling: [`dsh-trace.py`](dsh-trace.py) (the `watch` / `list` / `show` modes), this reference, and [`prompt-budget.md`](prompt-budget.md).

## Multi-agent hello world

The smallest run that proves delegation works end to end. The prompt is a versioned file, so the input is reproducible rather than living in someone's shell history:

```sh
cd ~/huawei2026/dsh-workspace                     # any workspace; it becomes the run's root
P=~/huawei2026/deepseek-harness/local/dsh-run
"$P/dsh-run.sh" batch "$(cat "$P/prompts/hello-world.md")"
```

[`prompts/hello-world.md`](prompts/hello-world.md) asks for three greetings, forbids the parent from writing any of them, and requires the three `subagent` calls to go out in one assistant message so the children run side by side.

```
1. English: Hello, how can I assist you today?
2. Chinese: 你好，今天有什么可以帮你的吗？
3. French: Bonjour, je suis votre assistant prêt à vous aider.

subagents: 3
```

Exit `0`, ~13 s, four sessions on disk. To watch it happen, start `./dsh-run.sh watch --since 5` in another terminal first; to read it afterwards, `./dsh-run.sh show`. What proves the delegation was real rather than the parent answering itself: the trajectory's `tool/result` events carry the children's greetings, not `started subagent <id>`.

## One-time setup

The checkout supplies the harness; `$DSH_HOME` (default `~/.dsh`) supplies the model route and the credential.

```sh
cd <checkout>
corepack pnpm install
corepack pnpm run build:lib     # enough for `batch`
corepack pnpm run build:web     # additionally required by `normal` and `trace`
```

`~/.dsh/settings.yaml` selects the provider route and the default model for every Agent an entry point creates — a subagent inherits it, so this one selection covers the whole tree:

```yaml
agent-default-model:
  provider: openrouter
  model: deepseek/deepseek-v4-flash

llm-pi-ai:
  providers:
    openrouter:
      apiKeyEnv: OPENROUTER_API_KEY
```

`apiKeyEnv` is a *reference* resolved per request, not the key: no secret belongs in this file. The key itself goes in `~/.dsh/.env` (mode `600`). Credentials resolve from the inherited environment first, then `$DSH_HOME/.credentials.yaml`, then the invoking directory's `.env`, then `$DSH_HOME/.env` — so an exported `OPENROUTER_API_KEY` wins over the file, and a per-project `.env` wins over the home one.

`openrouter` is a provider route the installed pi-ai catalog already ships, endpoint, wire protocol and `deepseek/*` model list included, which is why the route needs nothing but the credential reference. A gateway pi-ai has never heard of would instead declare `api`, `baseURL` and its own `models` list in the same place.

## Modes

```sh
./dsh-run.sh normal [app args...]        # web UI, stock session logs
./dsh-run.sh trace  [app args...]        # web UI, collection-mode session logs
./dsh-run.sh batch  "<job>"              # headless one-shot, collection-mode logs
./dsh-run.sh watch  [tracer args...]     # follow a live run's trajectory
./dsh-run.sh list   [tracer args...]     # recent runs, newest first
./dsh-run.sh show   [tracer args...]     # replay a finished run's trajectory
./dsh-run.sh dump   [normal|trace|batch] # print the composed tree, boot nothing
```

| Variable | Meaning | Default |
|---|---|---|
| `DSH_BIN` | How to invoke `dsh` | this checkout's TypeScript entry |
| `DSH_TRACE_ROOT` | Where the collecting modes write | `~/data/dsh-traces` |
| `DSH_HOME` | Harness home (settings, credentials, stock sessions) | `~/.dsh` |

An unset `DSH_BIN` runs the checkout's `apps/cli/src/bin.ts` through `tsx`, the same entry `pnpm dsh` uses, with one addition: `TSX_TSCONFIG_PATH` is pinned to the checkout. `tsx` resolves the repo's `@deepseek-ai/*` path aliases through the tsconfig it finds from the *process cwd*, and the cwd of an agent run is the workspace, not the checkout — without the pin, boot dies inside a half-resolved vendor package (`does not provide an export named 'FiberState'`). Pinning it is what lets a run start from any workspace without dropping a shim `tsconfig.json` there. Set `DSH_BIN` explicitly to use an installed build instead (`DSH_BIN='npx @deepseek-ai/dsh'`).

Run `./dsh-run.sh dump batch` before trusting a mode: the dump annotates every row with the file that supplied it, so an overlay winning on `tool-subagent` or `session-persistence-jsonl` is visible without booting anything.

## Submitting a task

**Headless, one task, one answer.** The task is the positional argument; the invoking directory is the workspace root.

```sh
cd ~/some/workspace
~/huawei2026/deepseek-harness/local/dsh-run/dsh-run.sh batch "summarize every TODO in this repo"
```

The runner creates one fresh persisted Agent, submits the task as an ordinary user message, waits for quiescence, and writes **the last non-empty assistant message** to stdout. Exit is `0` when the final `turn/end` reason is `completed` and `1` otherwise; a successful run keeps stderr empty and opens no listening port. Multi-line tasks work as a single quoted argument, and are the practical way to state a delegation contract (what to fan out, what each child must return, what the final line should look like).

Stdout carries only that final message, so anything the run should *report* has to end up in it — a task that wants per-child detail must say so. Everything else the run did is in the trajectory, not on stdout.

**Web UI, interactive.** `./dsh-run.sh trace` serves `http://127.0.0.1:3080` (`--port` to move it) with the same overlays applied, so an interactive session collects the same readable logs. `normal` is the same UI writing stock compressed logs.

## Synchronous delegation

Stock `dsh-base` mounts `tool-subagent` with `backgroundMode: continuable`, which makes an omitted `run_in_background` default to **true**. The parent gets back `started subagent <id>`, keeps going while the child runs, and needs a second round trip to collect anything — and a one-shot headless surface can reach quiescence and exit while children are still working.

[`foreground.patch.yml`](foreground.patch.yml) sets `enableRunInBackground: false` on both delegation tools. That closes both halves: the tool schema drops the `run_in_background` property, so the model cannot ask for background work, and `resolveDelegationRun` rejects the key at execution time even if a model emits it anyway. Verified against a real run's `request/header`, the `subagent` schema is exactly:

```
subagent schema properties: ['description', 'prompt']
```

Every delegation now blocks the calling tool call until the child's Activation settles, and **the child's final text is the tool result**. The parent aggregates from ordinary tool results inside one turn and the run ends with nothing left to collect. Sibling delegations issued in one assistant message still overlap under the loop's rolling tool-call pool, so "the host waits" costs the wall-clock of the slowest child, not their sum.

The overlay also disables `tool-subagent-control`, which registers `send_message`. That tool only addresses *continuable* children — children that outlive the tool call that started them — and foreground delegation cannot produce one, so it would be schema and prompt cost with no reachable subject. `list_agents` is a separate row and stays.

`job_list` / `job_output` / `job_kill` remain registered. They are the generic Task surface used by other background work (a long `bash` call, for instance), not a delegation route: with background delegation closed, no subagent can ever register a job for them to collect.

Delegation depth stays capped at the tool's default `maxDepth: 3`, so a child may delegate further and the trajectory nests accordingly.

## Watching a run live

Headless prints only the final message, and the web UI is the only built-in live surface. `watch` is the third option — it follows the collection-mode logs as they are written and renders every session in the run, parent and children, indented by delegation depth:

```sh
./dsh-run.sh watch --since 5          # terminal 1: start first
./dsh-run.sh batch "..."              # terminal 2: then run
```

```
[5f2e6dc7 d0] ◆ session root
[5f2e6dc7 d0] ▶ Multi-agent hello world. Do NOT write the greetings yourself …
[5f2e6dc7 d0] ⚙ route openrouter/deepseek/deepseek-v4-flash  ctx=1048575
  [30e9c97b d1] ◆ session subagent ← 5f2e6dc7
  [8cbb496d d1] ◆ session subagent ← 5f2e6dc7
  [d60567e3 d1] ◆ session subagent ← 5f2e6dc7
[5f2e6dc7 d0] → subagent({"description":"Greeting in English","prompt":"…")
  [8cbb496d d1] ⚙ route openrouter/deepseek/deepseek-v4-flash  ctx=1048575
  [d60567e3 d1] │ Bonjour.
  [d60567e3 d1] ■ turn 1 completed
[5f2e6dc7 d0] ← subagent: Hello
[5f2e6dc7 d0] ■ turn 1 completed
```

`◆` a session opening (`← parent`), `▶` a submitted message, `⚙` the model route, `│` streamed assistant text, `→` a tool call, `←` its result, `✗` an error, `■` a turn ending.

`--since S` also replays sessions touched in the last `S` seconds, so starting the watcher slightly late still shows the whole run; `--all` replays the entire root; `--interval` changes the 0.2 s poll. The watcher is a poller over an append-only file, not a subscription: the persistence backend coalesces writes in a ~200 ms window, so a child's `turn/end` can surface just after the parent's tool result even though it caused it. Timestamps in `show` are authoritative; live ordering is approximate.

## Where trajectories live

| Mode | Root | Encoding |
|---|---|---|
| `normal` (and any raw `dsh`) | `$DSH_HOME/sessions` | `session.jsonl.zstd`, packed chunk rows |
| `trace`, `batch` | `$DSH_TRACE_ROOT` | `session.jsonl`, one event per line |

```
<root>/
  --<normalized-cwd>--/          # the workspace, readable
    <session-id>/
      session.jsonl              # append-only, header line first
```

**One session is one file, and a subagent is its own session.** A multi-agent run is therefore a forest: the root session's header has `delegationDepth: 0` and no `parentSession`; every child header carries `origin: subagent`, `delegationDepth: N`, and `parentSession: <parent id>`. That link is the only thing tying a run together, and it is what all three reader subcommands follow.

The directory is the session id verbatim, and the two kinds are spelled differently: a root session's id carries a `session-` prefix (`session-f8263d60-…/`) while a child's is a bare UUID (`d9553bd6-…/`). A glob built from the short id that `list` and `show` display therefore matches children but silently misses the root — reach for `*/session.jsonl` and filter on the header instead.

A root holds one encoding. Startup discovery rejects a mismatched suffix rather than ignoring it, which is why the collecting modes need their own directory and why `dsh-run.sh` refuses a `DSH_TRACE_ROOT` under `$DSH_HOME/sessions`.

Files materialize lazily, on a session's first append — a created-but-silent session leaves nothing on disk.

## Reading a finished trajectory

```sh
./dsh-run.sh list                 # recent runs, newest first
./dsh-run.sh show                 # the newest run, fully
./dsh-run.sh show 5f2e6dc7        # by session id prefix (a child selects its run)
./dsh-run.sh show --raw           # the same timeline as raw event JSON
```

`list` gives one block per run: local time, short id, outcome, subagent count, title, model route, workspace.

`show` merges every session in the run into **one timestamped timeline**, which is what actually shows a parent waiting on its children, and closes with each session's final text. `--raw` swaps rendering for the underlying event JSON while keeping the same ordering and labels — the escape hatch for anything the renderer does not summarize.

Because collection mode is one JSON object per line, ordinary tools work too:

```sh
cd ~/data/dsh-traces/--Users-nickhe-huawei2026-dsh-workspace--

# what did the parent actually send each child?
jq -r 'select(.type=="tool/call" and .data.name=="subagent") | .data.arguments' */session.jsonl

# every model request route in the run
jq -r 'select(.type=="request/context") | "\(.data.provider) \(.data.model)"' */session.jsonl

# the exact system prompt and tool schemas one session was given
jq 'select(.type=="request/header") | .data.header | {system, tools: [.tools[].name]}' <id>/session.jsonl
```

`request/header` is the audit record worth knowing about: it holds the full system prompt and every tool schema as the model received them, which is how the `run_in_background` claim above is checked rather than assumed.

## Event vocabulary

| Type | Carries |
|---|---|
| `session` | Header line: `id`, `cwd`, `createdAt`, `parentSession`, `origin`, `delegationDepth` |
| `permission/preset`, `sandbox/mode` | The session's authority; a child records `source: delegation` |
| `user/message` | A submitted message. Every turn also re-sends a runtime-context snapshot |
| `request/header` | The complete request envelope: system prompt, tool schemas, model config |
| `request/context` | The resolved route: `provider`, `model`, `contextWindow` |
| `assistant/chunk` | Streaming deltas and `block-end` blocks (text, tool-call) |
| `tool/call` / `tool/result` | A dispatched call and its outcome, linked by `callId` |
| `turn/start`, `step/start`, `turn/end` | Loop structure; `turn/end.reason.kind` is the run's verdict |
| `session/title` | Fallback first, then a provider-generated title |

`seq` is contiguous across a decoded log (`events[i].seq === i`), so a gap means a truncated read, not a dropped event.

## Known limitations

- **The overlays are per-launch.** A raw `dsh --profile headless` bypasses `dsh-run.sh` and gets stock behavior back: background-by-default delegation and compressed packed logs. That is the deliberate tradeoff for keeping this tooling stateless. To make foreground delegation hold for *every* boot on a machine, raw invocations included, copy `foreground.patch.yml`'s rows into `$DSH_HOME/cordis.patch.yml` — that layer composes before the `--patch` layer, so the launcher's identical overlay lands on top of it idempotently rather than conflicting. Collection-mode logging stays per-launch either way, since a root holds one encoding. `dump` is how you confirm which you are about to get.
- **`normal` and `trace` need `pnpm run build:web`.** `batch` needs only `build:lib`. The launcher does not check artifact freshness, so a stale frontend bundle serves older browser code until rebuilt.
- **The watcher polls.** No filesystem-event subscription, no backpressure; live ordering is approximate within the persistence layer's write-batching window.
- **`dsh-trace.py` does not decode `.jsonl.zstd`.** Pointed at a stock root it says so and stops rather than half-reading it. Collect with `trace`/`batch`, or decompress first.
- **Trace roots grow without bound.** Nothing prunes `$DSH_TRACE_ROOT`; a run keeps every session it opened, including children.
