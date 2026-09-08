# `local/` — fork-only tooling

English | [中文](README.zh.md)

Nothing in this directory is part of DeepSeek Harness. It is workflow tooling for this fork, kept in the repository so it is versioned alongside the harness revision it was written against. Upstream does not accept external pull requests, so none of it is proposed there, and it should stay out of any change intended to be portable back.

## `dsh-run/`

A launcher that boots `dsh` in one of a few named modes, differing only in which patch overlays reach the composed config tree, plus a reader for what those runs produced.

```sh
local/dsh-run/dsh-run.sh normal [app args...]        # web UI, stock session logs
local/dsh-run/dsh-run.sh trace  [app args...]        # web UI, collection-mode session logs
local/dsh-run/dsh-run.sh batch  "<job>"              # headless one-shot, collection-mode logs
local/dsh-run/dsh-run.sh watch  [tracer args...]     # follow a live run's trajectory
local/dsh-run/dsh-run.sh list   [tracer args...]     # recent runs, newest first
local/dsh-run/dsh-run.sh show   [tracer args...]     # replay a finished run's trajectory
local/dsh-run/dsh-run.sh dump   [normal|trace|batch] # print the composed tree, boot nothing
```

`DSH_BIN` selects how `dsh` is invoked; unset, it runs this checkout's TypeScript entry. `DSH_TRACE_ROOT` selects where the collecting modes write (default `~/data/dsh-traces`).

**[`dsh-run/README.md`](dsh-run/README.md) is the interface reference** — setup, submitting a task, synchronous delegation, watching a run live, where trajectories live and how to read them.

### Why an overlay instead of a config edit

Both overlays arrive through `--patch`, which composes after the bundle layers, the profile's `cordis.patch.yml`, and the home-level `$DSH_HOME/cordis.patch.yml` ([layer order](../apps/cli/reference/README.md)). They therefore apply per launch: a stock run and a collecting run can alternate without either editing a shared file or leaving state behind for the next launch.

An id-targeted patch replaces the row's whole `config` rather than deep-merging keys, so each overlay restates every field of the rows it touches.

`foreground.patch.yml` closes background delegation. Stock `tool-subagent` runs `backgroundMode: continuable`, so an omitted `run_in_background` defaults to true, the parent gets back a child id instead of an answer, and a one-shot surface can exit while children are still working. `enableRunInBackground: false` drops the property from the tool schema and rejects it at execution time, which makes every delegation block its calling tool call and makes the child's final text the tool result. It also disables `tool-subagent-control`, whose `send_message` can only address continuable children.

`trace.patch.yml` changes three fields on `session-persistence-jsonl`:

| Field | Stock | Collecting | Why |
|---|---|---|---|
| `root` | `$DSH_HOME/sessions` | `$DSH_TRACE_ROOT` | A root holds one encoding; startup discovery rejects a mismatched suffix rather than ignoring it, so raw logs need their own directory. |
| `compression` | `zstd` | `none` | The default artifact is concatenated Zstandard frames, which a line-oriented reader cannot consume directly. |
| `packChunks` | `true` | `false` | The default collapses runs of three or more consecutive same-block `assistant/chunk` deltas into packed `text-chunks` / `reasoning-chunks` / `tool-call-chunks` rows, which a naive one-JSON-object-per-line reader mis-parses. |

### Guards

The script refuses a `DSH_TRACE_ROOT` under the default session root (the encoding conflict above) both before creating it and again after symlink resolution, refuses `batch` without a job string, fails loud when an overlay file is missing, and forwards no app arguments to `dump`, which `--dump-config` rejects. Run `dump batch` before a collecting session: the dump annotates each row with the file that supplied it, so both overlays winning are visible without booting.

`dsh-trace.py` reads only raw `.jsonl`. Pointed at a root holding `.jsonl.zstd` it names the mode mistake and stops rather than half-reading it.
