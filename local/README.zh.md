# `local/` — 仅属于本 fork 的工具

[English](README.md) | 中文

本目录中的任何内容都不属于 DeepSeek Harness。它是本 fork 的工作流工具，之所以放进仓库，是为了与它所针对的那个 harness 版本一起被版本化。上游不接受外部 pull request，因此这里的东西都不会提交上去，也不应混进任何打算移植回上游的改动。

## `dsh-run/`

一个启动器：以若干具名模式启动 `dsh`，模式之间的唯一差别是哪些 patch 覆盖层进入合成后的配置树；外加一个读取器，用来回读这些运行产出了什么。

```sh
local/dsh-run/dsh-run.sh normal [app args...]        # web UI, stock session logs
local/dsh-run/dsh-run.sh trace  [app args...]        # web UI, collection-mode session logs
local/dsh-run/dsh-run.sh batch  "<job>"              # headless one-shot, collection-mode logs
local/dsh-run/dsh-run.sh watch  [tracer args...]     # follow a live run's trajectory
local/dsh-run/dsh-run.sh list   [tracer args...]     # recent runs, newest first
local/dsh-run/dsh-run.sh show   [tracer args...]     # replay a finished run's trajectory
local/dsh-run/dsh-run.sh dump   [normal|trace|batch] # print the composed tree, boot nothing
```

`DSH_BIN` 决定如何调用 `dsh`；不设置时，运行本检出的 TypeScript 入口。`DSH_TRACE_ROOT` 决定采集模式写到哪里（默认 `~/data/dsh-traces`）。

**[`dsh-run/README.md`](dsh-run/README.md) 是接口参考** —— 环境准备、如何提交任务、同步委派、如何实时观察一次运行、轨迹在哪里以及怎么读。

### 为什么用覆盖层而不是改配置文件

两个覆盖层都通过 `--patch` 传入，它在 bundle 层、profile 的 `cordis.patch.yml` 和 home 级 `$DSH_HOME/cordis.patch.yml` 之后合成（[层级顺序](../apps/cli/reference/README.md)）。因此它们是逐次启动生效的：原样运行与采集运行可以交替进行，两者都不改动共享文件，也不给下一次启动留下状态。

id 定向 patch 会整体替换该行的 `config` 而非深度合并，因此每个覆盖层都会把它触及的行的每个字段重述一遍。

`foreground.patch.yml` 关掉后台委派。标准的 `tool-subagent` 跑在 `backgroundMode: continuable` 下，省略的 `run_in_background` 默认为真，父智能体拿回的是一个子 id 而不是答案，而单次界面可能在子智能体还在干活时就退出。`enableRunInBackground: false` 会把该属性从工具 schema 中去掉，并在执行期拒绝它，从而让每次委派都阻塞发起它的那次工具调用，并让子智能体的最终文本成为工具结果。它还禁用了 `tool-subagent-control`，因为其 `send_message` 只能面向可续子智能体。

`trace.patch.yml` 修改 `session-persistence-jsonl` 上的三个字段：

| 字段 | 原样 | 采集 | 原因 |
|---|---|---|---|
| `root` | `$DSH_HOME/sessions` | `$DSH_TRACE_ROOT` | 一个 root 只持有一种编码；启动发现阶段会拒绝后缀不匹配的产物，而不是忽略它，因此原始日志需要独立目录。 |
| `compression` | `zstd` | `none` | 默认产物是拼接的 Zstandard 帧，面向行的读取器无法直接消费。 |
| `packChunks` | `true` | `false` | 默认会把三条及以上连续同块 `assistant/chunk` 增量折叠成打包行 `text-chunks` / `reasoning-chunks` / `tool-call-chunks`，朴素的"一行一个 JSON 对象"读取器会解析错误。 |

### 护栏

脚本会拒绝位于默认会话根目录之下的 `DSH_TRACE_ROOT`（即上述编码冲突），创建之前拒绝一次、符号链接解析之后再拒绝一次；拒绝不带任务字符串的 `batch`；覆盖层文件缺失时明确报错；并且不向 `dump` 转发任何 app 参数——`--dump-config` 本身就拒绝这类调用。在采集会话之前先运行 `dump batch`：dump 会为每一行标注提供它的文件，因此无需启动即可看到两个覆盖层都已生效。

`dsh-trace.py` 只读原始 `.jsonl`。指向持有 `.jsonl.zstd` 的根目录时，它会指明这是模式用错了并停止，而不是读一半。
