#!/usr/bin/env bash
#
# Launch DeepSeek Harness in one of a few named modes.
#
# The only thing that varies between modes is which patch overlays go on the
# composed config tree and which profile boots. Nothing here mutates
# ~/.dsh/cordis.patch.yml, so a trace run and a normal run can coexist and
# neither leaves state behind for the next launch.
#
#   ./dsh-run.sh normal [app args...]        web UI, stock session logs
#   ./dsh-run.sh trace  [app args...]        web UI, collection-mode session logs
#   ./dsh-run.sh batch  "<job>"              headless one-shot, collection-mode logs
#   ./dsh-run.sh watch  [tracer args...]     follow a live run's trajectory
#   ./dsh-run.sh list   [tracer args...]     recent runs, newest first
#   ./dsh-run.sh show   [tracer args...]     replay a finished run's trajectory
#   ./dsh-run.sh dump   [normal|trace|batch] print the composed tree, boot nothing
#
# Every booting mode applies foreground.patch.yml, so delegation is synchronous:
# a subagent runs to completion inside its parent's tool call, the parent waits
# and aggregates from tool results, and the run ends with no background job left
# to collect. See that file for why the stock default is the other way round.
#
# Environment:
#   DSH_BIN         how to invoke dsh          (default: this checkout's TypeScript entry)
#   DSH_TRACE_ROOT  where trace mode writes    (default: ~/data/dsh-traces)
#   DSH_HOME        harness home               (dsh's own default: ~/.dsh)

set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "$HERE/../.." && pwd)"
TRACE_PATCH="$HERE/trace.patch.yml"
FOREGROUND_PATCH="$HERE/foreground.patch.yml"
TRACER="$HERE/dsh-trace.py"

DSH_TRACE_ROOT="${DSH_TRACE_ROOT:-$HOME/data/dsh-traces}"
DSH_SESSIONS_DEFAULT="${DSH_HOME:-$HOME/.dsh}/sessions"

die() { printf 'dsh-run: %s\n' "$1" >&2; exit 1; }

usage() {
  awk 'NR>2 && /^#/ { sub(/^# ?/, ""); print; next } NR>2 { exit }' "${BASH_SOURCE[0]}"
  exit "${1:-0}"
}

# How to invoke dsh. `dsh` is not always on PATH (it ships as a package bin, not
# a global), so an unset DSH_BIN prefers this checkout's TypeScript entry — the
# same thing `pnpm dsh` runs.
#
# Source execution needs one thing `pnpm dsh` gets for free: tsx resolves the
# repo's `@deepseek-ai/*` path aliases through the tsconfig it finds from the
# PROCESS CWD, and the whole point of a workspace-rooted agent is that the cwd
# is the workspace, not the checkout. Without the alias table, boot dies on a
# half-resolved vendor package ("does not provide an export named 'FiberState'").
# TSX_TSCONFIG_PATH pins the table to this checkout, so any cwd works and the
# workspace stays free of a shim tsconfig.json.
if [[ -n "${DSH_BIN:-}" ]]; then
  # Accept a multi-word launcher, e.g. DSH_BIN="npx @deepseek-ai/dsh".
  read -r -a DSH_CMD <<< "$DSH_BIN"
  DSH_SOURCE_ENTRY=
elif [[ -f "$REPO/apps/cli/src/bin.ts" && -f "$REPO/node_modules/tsx/dist/esm/index.mjs" ]]; then
  DSH_CMD=(node --import "file://$REPO/node_modules/tsx/dist/esm/index.mjs" "$REPO/apps/cli/src/bin.ts")
  DSH_SOURCE_ENTRY=1
  export TSX_TSCONFIG_PATH="$REPO/tsconfig.json"
else
  read -r -a DSH_CMD <<< dsh
  DSH_SOURCE_ENTRY=
fi

check_bin() {
  if [[ -n "$DSH_SOURCE_ENTRY" ]]; then
    [[ -d "$REPO/node_modules" ]] || die "checkout has no node_modules — run 'pnpm install && pnpm run build:lib' in $REPO"
    return
  fi
  command -v "${DSH_CMD[0]}" >/dev/null 2>&1 \
    || die "cannot find '${DSH_CMD[0]}' on PATH. Set DSH_BIN, e.g. DSH_BIN='npx @deepseek-ai/dsh'"
}

require_patch() {
  [[ -f "$1" ]] || die "missing overlay: $1"
}

# One root holds one encoding. Trace mode writes raw .jsonl; the default root
# holds .jsonl.zstd, and startup discovery rejects the mismatched suffix rather
# than ignoring it — so refuse the overlap here with a clearer message.
#
# The root is created before it is resolved: `cd "$(dirname X)"` on an absent
# parent yields an empty prefix inside the command substitution WITHOUT failing
# the assignment, so the earlier form silently resolved ~/data/dsh-traces to
# /dsh-traces on a machine where ~/data did not exist yet.
prepare_trace_root() {
  require_patch "$TRACE_PATCH"
  # Checked before creating anything, so a mistargeted root is refused rather
  # than materialized inside the session root it must not share.
  [[ "$DSH_TRACE_ROOT" != "$DSH_SESSIONS_DEFAULT"* ]] \
    || die "DSH_TRACE_ROOT must not live under the default session root ($DSH_SESSIONS_DEFAULT): one root holds one encoding"
  mkdir -p "$DSH_TRACE_ROOT" || die "cannot create DSH_TRACE_ROOT: $DSH_TRACE_ROOT"
  local resolved
  resolved="$(cd -- "$DSH_TRACE_ROOT" && pwd)" || die "DSH_TRACE_ROOT is not a directory: $DSH_TRACE_ROOT"
  # Again after symlink resolution, which can land somewhere the string did not.
  [[ "$resolved" != "$DSH_SESSIONS_DEFAULT"* ]] \
    || die "DSH_TRACE_ROOT resolves under the default session root ($DSH_SESSIONS_DEFAULT): one root holds one encoding"
  DSH_TRACE_ROOT="$resolved"
  export DSH_TRACE_ROOT
  printf 'dsh-run: traces -> %s  (raw .jsonl, one event per line)\n' "$DSH_TRACE_ROOT" >&2
}

run_tracer() {
  [[ -f "$TRACER" ]] || die "missing tracer: $TRACER"
  command -v python3 >/dev/null 2>&1 || die "watch/show need python3 on PATH"
  export DSH_TRACE_ROOT
  exec python3 "$TRACER" "$@"
}

mode="${1:-}"; shift || true
case "$mode" in
  normal)
    check_bin; require_patch "$FOREGROUND_PATCH"
    printf 'dsh-run: traces -> %s  (.jsonl.zstd, packed chunks)\n' "$DSH_SESSIONS_DEFAULT" >&2
    exec "${DSH_CMD[@]}" --profile web --patch "$FOREGROUND_PATCH" "$@"
    ;;
  trace)
    check_bin; require_patch "$FOREGROUND_PATCH"; prepare_trace_root
    exec "${DSH_CMD[@]}" --profile web --patch "$FOREGROUND_PATCH" --patch "$TRACE_PATCH" "$@"
    ;;
  batch)
    [[ $# -ge 1 && -n "$1" ]] || die 'batch needs a job string: ./dsh-run.sh batch "run the tests"'
    check_bin; require_patch "$FOREGROUND_PATCH"; prepare_trace_root
    exec "${DSH_CMD[@]}" --profile headless --patch "$FOREGROUND_PATCH" --patch "$TRACE_PATCH" "$@"
    ;;
  watch)
    run_tracer watch "$@"
    ;;
  list)
    run_tracer list "$@"
    ;;
  show)
    run_tracer show "$@"
    ;;
  dump)
    # --dump-config takes no app arguments, so this deliberately forwards none.
    check_bin; require_patch "$FOREGROUND_PATCH"
    case "${1:-normal}" in
      normal) exec "${DSH_CMD[@]}" --profile web --patch "$FOREGROUND_PATCH" --dump-config ;;
      trace)  prepare_trace_root
              exec "${DSH_CMD[@]}" --profile web --patch "$FOREGROUND_PATCH" --patch "$TRACE_PATCH" --dump-config ;;
      batch)  prepare_trace_root
              exec "${DSH_CMD[@]}" --profile headless --patch "$FOREGROUND_PATCH" --patch "$TRACE_PATCH" --dump-config ;;
      *) die "dump takes 'normal', 'trace' or 'batch', got '$1'" ;;
    esac
    ;;
  ''|-h|--help|help) usage 0 ;;
  *) printf 'dsh-run: unknown mode %s\n\n' "$mode" >&2; usage 1 ;;
esac
