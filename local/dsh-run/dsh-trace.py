#!/usr/bin/env python3
"""Read DeepSeek Harness collection-mode session logs.

`dsh --profile headless` prints one thing: the last non-empty assistant message.
Everything else a run did — which subagents it started, what it asked them, what
they answered, which model each one used — is only in the session log, and in
stock configuration that log is concatenated Zstandard frames with packed chunk
rows. `trace.patch.yml` turns it into one JSON object per line; this reads it.

    dsh-trace.py watch [--root R] [--since S | --all]   follow runs live
    dsh-trace.py list  [--root R] [-n N]                recent runs, newest first
    dsh-trace.py show  [--root R] [SESSION] [--raw]     one finished run

A run is one root session (`delegationDepth: 0`) plus every session that names it
transitively through `parentSession` — a subagent's transcript is its own file,
so a multi-agent run is a forest of them and all three subcommands stitch it
back together by that link.

Stdlib only, no build step: this reads an on-disk format, and a reader that
needs the workspace installed is a reader you cannot point at an archived trace.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- presentation

RESET, DIM, BOLD = "\033[0m", "\033[2m", "\033[1m"
# One color per delegation depth, reused past the end. Depth is the thing worth
# telling apart at a glance in a multi-agent log; session identity is the label.
DEPTH_COLORS = ["\033[36m", "\033[32m", "\033[33m", "\033[35m", "\033[34m"]

_color_enabled = True


def color(text: str, code: str) -> str:
    return f"{code}{text}{RESET}" if _color_enabled else text


def truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ------------------------------------------------------------------- the model


class SessionView:
    """One session's identity plus the streaming state its rendering needs."""

    def __init__(self, header: dict, path: Path):
        self.path = path
        self.id: str = header.get("id", path.parent.name)
        self.parent: str | None = header.get("parentSession")
        self.depth: int = header.get("delegationDepth", 0)
        self.cwd: str | None = header.get("cwd")
        self.created: int = header.get("createdAt", 0)
        self.origin: str | None = header.get("origin")
        self.title: str | None = None
        self.route: str | None = None
        self.outcome: str | None = None
        self.final_text: str | None = None
        # callId -> tool name, so a result can name the call it answers.
        self.calls: dict[str, str] = {}
        # (block index) -> buffered text-delta not yet emitted.
        self.buffers: dict[int, str] = {}

    @property
    def short(self) -> str:
        return self.id.replace("session-", "")[:8]

    @property
    def label(self) -> str:
        pad = "  " * self.depth
        tag = f"{pad}[{self.short} d{self.depth}]"
        return color(tag, DEPTH_COLORS[self.depth % len(DEPTH_COLORS)])


def read_header(path: Path) -> dict | None:
    """The first line of a transcript, or None while it is not yet readable."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = handle.readline()
        if not first.endswith("\n"):
            return None  # header still being written
        header = json.loads(first)
        return header if header.get("type") == "session" else None
    except (OSError, json.JSONDecodeError):
        return None


def find_transcripts(root: Path) -> list[Path]:
    """Every session transcript under a collection-mode root.

    Layout is `<root>/--<normalized-cwd>--/<encoded-id>/session.jsonl`. A run in
    stock configuration writes `session.jsonl.zstd` instead; those are skipped
    rather than reported, because a root holds one encoding and pointing this at
    the default `~/.dsh/sessions` is a mode mistake the caller should hear about
    once, from `require_trace_root`, not once per file.
    """
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*/session.jsonl"))


def require_trace_root(root: Path, *, must_exist: bool = True) -> None:
    if not root.is_dir():
        if must_exist:
            sys.exit(f"dsh-trace: no such trace root: {root}")
        return  # watch may legitimately start before the first run creates it
    if not find_transcripts(root) and list(root.glob("*/*/session.jsonl.zstd")):
        sys.exit(
            f"dsh-trace: {root} holds compressed logs (.jsonl.zstd), which this reader does not decode.\n"
            "           Collect with `dsh-run.sh trace|batch` (compression: none) or point --root at that output."
        )


# ----------------------------------------------------------------- event lines


def render(view: SessionView, event: dict, opts: argparse.Namespace) -> list[str]:
    """Zero or more display lines for one event. Streaming state lives on view."""
    kind = event.get("type", "")
    data = event.get("data", {}) or {}
    out: list[str] = []

    def emit(marker: str, text: str) -> None:
        out.append(f"{view.label} {marker} {text}")

    if kind == "request/context":
        view.route = f"{data.get('provider')}/{data.get('model')}"
        emit(color("⚙", DIM), color(f"route {view.route}  ctx={data.get('contextWindow')}", DIM))

    elif kind == "session/title" and data.get("title"):
        view.title = data["title"]

    elif kind == "user/message":
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        # Every turn re-sends a runtime-context snapshot; it is composition, not
        # something a person or a parent agent said.
        if text and not text.startswith("Current runtime context"):
            emit(color("▶", BOLD), truncate(text, opts.width))

    elif kind == "assistant/chunk":
        chunk = data.get("chunk", {})
        ctype = chunk.get("type")
        index = chunk.get("index", 0)
        if ctype == "text-delta":
            buffered = view.buffers.get(index, "") + chunk.get("text", "")
            # Flush on a line break or once a line's worth has accumulated, so
            # the stream reads live without one line per token.
            while "\n" in buffered:
                line, buffered = buffered.split("\n", 1)
                if line.strip():
                    emit("│", line.strip())
            if len(buffered) >= opts.width:
                emit("│", buffered.strip())
                buffered = ""
            view.buffers[index] = buffered
        elif ctype == "block-end":
            leftover = view.buffers.pop(index, "")
            if leftover.strip():
                emit("│", leftover.strip())
            block = chunk.get("block", {})
            if block.get("type") == "text" and block.get("text", "").strip():
                view.final_text = block["text"].strip()

    elif kind == "tool/call":
        name = data.get("name", "?")
        view.calls[data.get("callId", "")] = name
        emit(color("→", BOLD), f"{color(name, BOLD)}({truncate(data.get('arguments', ''), opts.width)})")

    elif kind == "tool/result":
        message = data.get("message", {})
        pieces, is_error = [], False
        for block in message.get("content", []):
            if block.get("type") != "tool-result":
                continue
            is_error = is_error or bool(block.get("isError"))
            name = view.calls.get(block.get("toolCallId", ""), "tool")
            body = "".join(c.get("text", "") for c in block.get("content", []) if c.get("type") == "text")
            pieces.append((name, body))
        for name, body in pieces:
            marker = color("✗", "\033[31m") if is_error else color("←", DIM)
            emit(marker, f"{name}: {truncate(body, opts.width)}")

    elif kind == "turn/end":
        reason = (data.get("reason") or {}).get("kind", "?")
        view.outcome = reason
        ok = reason == "completed"
        emit(color("■", "\033[32m" if ok else "\033[31m"), f"turn {data.get('turn')} {reason}")

    elif "error" in kind:
        emit(color("✗", "\033[31m"), f"{kind}: {truncate(json.dumps(data), opts.width)}")

    return out


def announce(view: SessionView) -> str:
    parent = f" ← {view.parent.replace('session-', '')[:8]}" if view.parent else ""
    origin = f" {view.origin}" if view.origin else " root"
    return f"{view.label} {color('◆', BOLD)}{color(f' session{origin}{parent}', DIM)}"


# ------------------------------------------------------------------ subcommand: watch


def cmd_watch(opts: argparse.Namespace) -> int:
    root = opts.root
    # Starting the watcher before the run is the normal order, and the backend
    # materializes a session directory lazily (first append), so an absent root
    # is "not yet", not an error.
    require_trace_root(root, must_exist=False)
    cutoff = 0.0 if opts.all else time.time() - opts.since

    print(
        color(f"dsh-trace: watching {root}", DIM)
        + color("  (ctrl-c to stop)" if sys.stdout.isatty() else "", DIM),
        file=sys.stderr,
        flush=True,
    )

    views: dict[Path, SessionView] = {}
    handles: dict[Path, object] = {}
    skipped: set[Path] = set()

    try:
        while True:
            for path in find_transcripts(root):
                if path in handles or path in skipped:
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        skipped.add(path)
                        continue
                except OSError:
                    continue
                header = read_header(path)
                if header is None:
                    continue  # header not flushed yet; retry next tick
                view = SessionView(header, path)
                views[path] = view
                handle = path.open("r", encoding="utf-8")
                handle.readline()  # consume the header line
                handles[path] = handle
                print(announce(view), flush=True)

            for path, handle in list(handles.items()):
                view = views[path]
                while True:
                    where = handle.tell()
                    line = handle.readline()
                    if not line.endswith("\n"):
                        # Partial write: rewind so the next tick reads it whole.
                        handle.seek(where)
                        break
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for rendered in render(view, event, opts):
                        print(rendered, flush=True)

            time.sleep(opts.interval)
    except KeyboardInterrupt:
        print(color("\ndsh-trace: stopped", DIM), file=sys.stderr)
        return 0
    finally:
        for handle in handles.values():
            handle.close()  # type: ignore[attr-defined]


# ------------------------------------------------------- subcommand: list / show


def load_all(root: Path, opts: argparse.Namespace) -> dict[str, SessionView]:
    """Every session under the root, replayed so its summary fields are filled."""
    views: dict[str, SessionView] = {}
    for path in find_transcripts(root):
        header = read_header(path)
        if header is None:
            continue
        view = SessionView(header, path)
        views[view.id] = view
        with path.open("r", encoding="utf-8") as handle:
            handle.readline()
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                render(view, event, opts)  # discard lines; keep the state
    return views


def descendants(views: dict[str, SessionView], root_id: str) -> list[SessionView]:
    """The run rooted at root_id, in creation order."""
    picked = [v for v in views.values() if v.id == root_id]
    frontier = {root_id}
    while frontier:
        children = [v for v in views.values() if v.parent in frontier and v not in picked]
        if not children:
            break
        picked.extend(children)
        frontier = {v.id for v in children}
    return sorted(picked, key=lambda v: v.created)


def cmd_list(opts: argparse.Namespace) -> int:
    require_trace_root(opts.root)
    views = load_all(opts.root, opts)
    roots = sorted((v for v in views.values() if v.depth == 0), key=lambda v: v.created, reverse=True)
    if not roots:
        print(f"dsh-trace: no runs under {opts.root}", file=sys.stderr)
        return 1
    for view in roots[: opts.number]:
        kids = len(descendants(views, view.id)) - 1
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(view.created / 1000))
        print(
            f"{stamp}  {color(view.short, BOLD)}  "
            f"{(view.outcome or 'running'):<10} "
            f"{kids} subagent(s)  {truncate(view.title or '(untitled)', 48)}"
        )
        print(color(f"                       {view.route or '?'}  {view.cwd or ''}", DIM))
    return 0


def cmd_show(opts: argparse.Namespace) -> int:
    require_trace_root(opts.root)
    views = load_all(opts.root, opts)
    roots = sorted((v for v in views.values() if v.depth == 0), key=lambda v: v.created, reverse=True)
    if opts.session:
        matches = [v for v in views.values() if v.id.startswith(opts.session) or v.short.startswith(opts.session)]
        if not matches:
            sys.exit(f"dsh-trace: no session matching '{opts.session}' under {opts.root}")
        target = matches[0]
        # Naming a child shows the run it belongs to, not a severed subtree.
        while target.parent and target.parent in views:
            target = views[target.parent]
    elif roots:
        target = roots[0]
    else:
        sys.exit(f"dsh-trace: no runs under {opts.root}")

    members = descendants(views, target.id)
    print(color(f"run {target.short}  {len(members) - 1} subagent(s)  {target.title or ''}", BOLD))
    print(color(f"{target.cwd or ''}", DIM))
    print()

    # One merged timeline. Each event is stamped, so interleaving parent and
    # child by time is what actually shows a parent waiting on its children.
    timeline: list[tuple[int, SessionView, dict]] = []
    for view in members:
        view.buffers.clear()
        view.calls.clear()
        with view.path.open("r", encoding="utf-8") as handle:
            header_line = handle.readline()
            try:
                timeline.append((json.loads(header_line).get("createdAt", 0), view, {"type": "__session__"}))
            except json.JSONDecodeError:
                pass
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                timeline.append((event.get("time", 0), view, event))
    timeline.sort(key=lambda item: item[0])

    for stamp, view, event in timeline:
        prefix = color(time.strftime("%H:%M:%S", time.localtime(stamp / 1000)) if stamp else "        ", DIM)
        if event.get("type") == "__session__":
            print(f"{prefix} {announce(view)}")
            continue
        if opts.raw:
            print(f"{prefix} {view.label} {json.dumps(event)[: opts.width]}")
            continue
        for rendered in render(view, event, opts):
            print(f"{prefix} {rendered}")

    print()
    for view in members:
        if view.final_text:
            print(f"{view.label} {color('final', BOLD)}: {truncate(view.final_text, 200)}")
    return 0


# -------------------------------------------------------------------------- cli


def main(argv: list[str]) -> int:
    global _color_enabled

    default_root = os.environ.get("DSH_TRACE_ROOT") or str(Path.home() / "data" / "dsh-traces")

    # Shared options live on a parent parser so they are accepted on EITHER side
    # of the subcommand: `dsh-run.sh watch --no-color` forwards trailing flags
    # after the subcommand, and argparse only binds a top-level option before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=Path(default_root), help="trace root (default: $DSH_TRACE_ROOT)")
    common.add_argument("--width", type=int, default=140, help="truncate rendered text at N characters")
    common.add_argument("--no-color", action="store_true", help="disable ANSI color")

    parser = argparse.ArgumentParser(
        prog="dsh-trace.py", parents=[common], description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    watch = sub.add_parser("watch", parents=[common], help="follow runs as they happen")
    watch.add_argument("--since", type=float, default=120.0, help="also replay sessions touched in the last S seconds")
    watch.add_argument("--all", action="store_true", help="replay every session in the root first")
    watch.add_argument("--interval", type=float, default=0.2, help="poll interval in seconds")
    watch.set_defaults(func=cmd_watch)

    listing = sub.add_parser("list", parents=[common], help="recent runs, newest first")
    listing.add_argument("-n", "--number", type=int, default=10)
    listing.set_defaults(func=cmd_list)

    show = sub.add_parser("show", parents=[common], help="replay one finished run")
    show.add_argument("session", nargs="?", help="session id prefix (default: the newest run)")
    show.add_argument("--raw", action="store_true", help="print raw event JSON instead of rendered lines")
    show.set_defaults(func=cmd_show)

    opts = parser.parse_args(argv)
    _color_enabled = sys.stdout.isatty() and not opts.no_color
    return opts.func(opts)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
