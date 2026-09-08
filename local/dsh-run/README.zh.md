# `dsh-run/` —— 提交、实时观察、回读多智能体运行

[English](README.md) | 中文

`dsh-run.sh` 就是全部接口：它以若干具名模式启动 DeepSeek Harness，并把一次运行做过的事读回来。模式之间的差别只有两点——哪些 `--patch` 覆盖层进入合成后的配置树，以及启动哪个 profile；因此这里不改任何共享文件，任何模式都不会给下一次启动留下状态。

真正起作用的是两个覆盖层。[`foreground.patch.yml`](foreground.patch.yml) 让委派变成同步的——子智能体在父智能体的那次工具调用内部跑完。[`trace.patch.yml`](trace.patch.yml) 让 session 日志可读——每行一个 JSON 对象，而不是塞在 Zstandard 帧里的打包行。[`dsh-trace.py`](dsh-trace.py) 负责读取结果，实时读或事后读都可以。

## 一次性准备

仓库检出提供 harness 本身；`$DSH_HOME`（默认 `~/.dsh`）提供模型路由和凭据。

```sh
cd <checkout>
corepack pnpm install
corepack pnpm run build:lib     # enough for `batch`
corepack pnpm run build:web     # additionally required by `normal` and `trace`
```

`~/.dsh/settings.yaml` 选定 provider 路由，以及任何入口点创建 Agent 时的默认模型——子智能体会继承它，所以这一处选择覆盖整棵树：

```yaml
agent-default-model:
  provider: openrouter
  model: deepseek/deepseek-v4-flash

llm-pi-ai:
  providers:
    openrouter:
      apiKeyEnv: OPENROUTER_API_KEY
```

`apiKeyEnv` 是一个每次请求时解析的*引用*，不是密钥本身：这个文件里不该出现任何秘密。密钥放在 `~/.dsh/.env`（权限 `600`）。凭据的解析顺序是：继承来的环境变量、`$DSH_HOME/.credentials.yaml`、调用目录下的 `.env`、最后是 `$DSH_HOME/.env`——所以导出的 `OPENROUTER_API_KEY` 会压过文件，项目级 `.env` 会压过 home 级的那个。

`openrouter` 是已安装的 pi-ai catalog 自带的一条 provider 路由，端点、线协议、`deepseek/*` 模型表都在里面，所以这条路由除了凭据引用之外什么都不用写。若换成 pi-ai 不认识的网关，则要在同一处声明 `api`、`baseURL` 和自己的 `models` 列表。

## 模式

```sh
./dsh-run.sh normal [app args...]        # web UI, stock session logs
./dsh-run.sh trace  [app args...]        # web UI, collection-mode session logs
./dsh-run.sh batch  "<job>"              # headless one-shot, collection-mode logs
./dsh-run.sh watch  [tracer args...]     # follow a live run's trajectory
./dsh-run.sh list   [tracer args...]     # recent runs, newest first
./dsh-run.sh show   [tracer args...]     # replay a finished run's trajectory
./dsh-run.sh dump   [normal|trace|batch] # print the composed tree, boot nothing
```

| 变量 | 含义 | 默认值 |
|---|---|---|
| `DSH_BIN` | 如何调用 `dsh` | 本检出的 TypeScript 入口 |
| `DSH_TRACE_ROOT` | 采集模式写到哪里 | `~/data/dsh-traces` |
| `DSH_HOME` | harness home（设置、凭据、标准 session） | `~/.dsh` |

不设 `DSH_BIN` 时，脚本通过 `tsx` 运行本检出的 `apps/cli/src/bin.ts`，也就是 `pnpm dsh` 用的那个入口，只多做一件事：把 `TSX_TSCONFIG_PATH` 钉到这个检出上。`tsx` 是从*进程 cwd* 开始找 tsconfig 来解析仓库的 `@deepseek-ai/*` 路径别名的，而一次 agent 运行的 cwd 是工作区、不是检出目录——不钉住的话，启动会死在一个只解析了一半的 vendor 包上（`does not provide an export named 'FiberState'`）。钉住它，才使得运行可以从任意工作区启动，而不必在那里丢一个垫片 `tsconfig.json`。想改用已安装的构建产物，显式设置 `DSH_BIN`（`DSH_BIN='npx @deepseek-ai/dsh'`）。

信任某个模式之前先跑 `./dsh-run.sh dump batch`：dump 会给每一行标注它来自哪个文件，于是覆盖层在 `tool-subagent` 或 `session-persistence-jsonl` 上的胜出结果，不启动任何东西就能看见。

## 提交一个任务

**Headless，一个任务，一个答案。** 任务是位置参数；调用目录就是工作区根。

```sh
cd ~/some/workspace
~/huawei2026/deepseek-harness/local/dsh-run/dsh-run.sh batch "summarize every TODO in this repo"
```

runner 会新建一个持久化 Agent，把任务当作一条普通用户消息提交，等到静默，然后把**最后一条非空 assistant 消息**写到 stdout。最终 `turn/end` 的原因是 `completed` 时退出码为 `0`，否则为 `1`；成功的运行不写 stderr，也不监听任何端口。多行任务作为一个带引号的参数即可，而这正是表达委派契约的实用方式（要扇出什么、每个子智能体必须返回什么、最后一行长什么样）。

stdout 只有那条最终消息，所以运行需要*汇报*的东西必须落进它里面——想要逐个子智能体的细节，任务里就得这么要求。运行做过的其余一切都在轨迹里，不在 stdout 上。

**Web UI，交互式。** `./dsh-run.sh trace` 在 `http://127.0.0.1:3080` 提供服务（`--port` 可改），覆盖层同样生效，因此交互会话采集到的是同样可读的日志。`normal` 是同一个 UI，但写标准的压缩日志。

## 同步委派

标准 `dsh-base` 挂载 `tool-subagent` 时用的是 `backgroundMode: continuable`，这会让省略掉的 `run_in_background` 默认为**真**。父智能体拿回一句 `started subagent <id>` 就继续往下走，要拿到任何结果还得再来一轮；而 headless 这种单次界面，完全可能在子智能体还在干活的时候就到达静默并退出。

[`foreground.patch.yml`](foreground.patch.yml) 在两个委派工具上都设了 `enableRunInBackground: false`。这堵死了两半：工具 schema 会去掉 `run_in_background` 属性，模型无从请求后台执行；即使模型硬要发出这个键，`resolveDelegationRun` 也会在执行期拒绝。对着一次真实运行的 `request/header` 核对，`subagent` 的 schema 恰好是：

```
subagent schema properties: ['description', 'prompt']
```

于是每次委派都会阻塞发起它的那次工具调用，直到子智能体的 Activation 结束，而且**子智能体的最终文本就是工具结果**。父智能体在同一个 turn 内从普通工具结果里做聚合，运行结束时没有任何东西留待回收。同一条 assistant 消息里发出的多个兄弟委派仍然在循环的滚动工具调用池中并行，所以"host 停下来等"付出的是最慢那个子智能体的墙钟时间，而不是它们之和。

该覆盖层还禁用了 `tool-subagent-control`，也就是注册 `send_message` 的那一行。那个工具只面向*可续*子智能体——即活得比启动它的工具调用更久的子智能体——而前台委派根本产生不出这种子智能体，所以它只会是没有可及对象的 schema 与提示词开销。`list_agents` 是独立的一行，保留。

`job_list` / `job_output` / `job_kill` 仍然注册着。它们是供其它后台工作（比如一次长时间的 `bash`）使用的通用 Task 界面，不是委派通道：后台委派既已关闭，就没有任何子智能体能注册出让它们去回收的 job。

委派深度仍受工具默认的 `maxDepth: 3` 约束，因此子智能体可以继续往下委派，轨迹也会相应嵌套。

## 实时观察一次运行

headless 只打印最终消息，而 web UI 是唯一内置的实时界面。`watch` 是第三个选择——它跟随采集模式日志的写入，渲染这次运行里的每一个 session，父与子按委派深度缩进：

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

`◆` 一个 session 开启（`← 父`），`▶` 一条提交的消息，`⚙` 模型路由，`│` 流式 assistant 文本，`→` 一次工具调用，`←` 它的结果，`✗` 一个错误，`■` 一个 turn 结束。

`--since S` 会额外重放最近 `S` 秒内被写过的 session，所以观察器起晚一点也仍能看到整次运行；`--all` 重放整个根目录；`--interval` 改变 0.2 秒的轮询间隔。观察器是对追加写文件的轮询，不是订阅：持久化后端会在约 200 毫秒的窗口内合并写入，因此子智能体的 `turn/end` 可能紧跟在父智能体的工具结果之后才浮现，尽管前者才是后者的成因。`show` 里的时间戳是权威的，实时顺序则是近似的。

## 轨迹在哪里

| 模式 | 根目录 | 编码 |
|---|---|---|
| `normal`（以及任何裸 `dsh`） | `$DSH_HOME/sessions` | `session.jsonl.zstd`，打包 chunk 行 |
| `trace`、`batch` | `$DSH_TRACE_ROOT` | `session.jsonl`，每行一个事件 |

```
<root>/
  --<normalized-cwd>--/          # the workspace, readable
    <session-id>/
      session.jsonl              # append-only, header line first
```

**一个 session 一个文件，而一个子智能体就是一个 session。** 因此一次多智能体运行是一片森林：根 session 的 header 里 `delegationDepth: 0` 且没有 `parentSession`；每个子 header 带着 `origin: subagent`、`delegationDepth: N` 和 `parentSession: <父 id>`。这条链接是把一次运行串起来的唯一线索，三个读取子命令跟的都是它。

目录名就是 session id 本身，而两类 id 的写法不同：根 session 的 id 带 `session-` 前缀（`session-f8263d60-…/`），子 session 的则是裸 UUID（`d9553bd6-…/`）。因此用 `list` 和 `show` 显示的短 id 拼出来的 glob 只会匹配到子 session，而悄悄漏掉根——请改用 `*/session.jsonl` 再按 header 过滤。

一个根目录只承载一种编码。启动时的发现逻辑会拒绝不匹配的后缀而不是忽略它，这既是采集模式需要自己目录的原因，也是 `dsh-run.sh` 拒绝把 `DSH_TRACE_ROOT` 设在 `$DSH_HOME/sessions` 之下的原因。

文件是惰性落盘的，发生在一个 session 第一次追加时——创建了却一直沉默的 session 在磁盘上什么都不留。

## 回读一次已完成的轨迹

```sh
./dsh-run.sh list                 # recent runs, newest first
./dsh-run.sh show                 # the newest run, fully
./dsh-run.sh show 5f2e6dc7        # by session id prefix (a child selects its run)
./dsh-run.sh show --raw           # the same timeline as raw event JSON
```

`list` 每次运行给一块：本地时间、短 id、结果、子智能体数量、标题、模型路由、工作区。

`show` 把这次运行里的每个 session 合并成**一条带时间戳的时间线**，这才真正显示出父智能体在等子智能体，末尾附上每个 session 的最终文本。`--raw` 保留同样的顺序与标签，只把渲染换成底层事件 JSON——渲染器没有概括到的东西，从这里出去看。

因为采集模式是每行一个 JSON 对象，普通工具也能直接用：

```sh
cd ~/data/dsh-traces/--Users-nickhe-huawei2026-dsh-workspace--

# what did the parent actually send each child?
jq -r 'select(.type=="tool/call" and .data.name=="subagent") | .data.arguments' */session.jsonl

# every model request route in the run
jq -r 'select(.type=="request/context") | "\(.data.provider) \(.data.model)"' */session.jsonl

# the exact system prompt and tool schemas one session was given
jq 'select(.type=="request/header") | .data.header | {system, tools: [.tools[].name]}' <id>/session.jsonl
```

`request/header` 是值得知道的那条审计记录：它保存了模型收到的完整系统提示词和每一个工具 schema，上面关于 `run_in_background` 的结论正是这样核对出来的，而不是假定出来的。

## 事件词汇表

| 类型 | 承载内容 |
|---|---|
| `session` | header 行：`id`、`cwd`、`createdAt`、`parentSession`、`origin`、`delegationDepth` |
| `permission/preset`、`sandbox/mode` | 该 session 的权限；子 session 会记录 `source: delegation` |
| `user/message` | 一条提交的消息。每个 turn 还会重发一份运行时上下文快照 |
| `request/header` | 完整的请求信封：系统提示词、工具 schema、模型配置 |
| `request/context` | 解析后的路由：`provider`、`model`、`contextWindow` |
| `assistant/chunk` | 流式增量与 `block-end` 块（文本、工具调用） |
| `tool/call` / `tool/result` | 一次派发的调用及其结果，用 `callId` 关联 |
| `turn/start`、`step/start`、`turn/end` | 循环结构；`turn/end.reason.kind` 是这次运行的判定 |
| `session/title` | 先是兜底标题，随后是模型生成的标题 |

`seq` 在解码后的日志里是连续的（`events[i].seq === i`），所以出现空缺意味着读取被截断，而不是事件被丢弃。

## 已知限制

- **覆盖层是按次启动生效的。** 裸跑 `dsh --profile headless` 绕过 `dsh-run.sh`，就回到标准行为：默认后台的委派，以及压缩打包的日志。这是让本套工具保持无状态所换来的、有意为之的取舍。若要让前台委派对一台机器上的*每一次*启动都生效（包括裸调用），把 `foreground.patch.yml` 的那几行复制进 `$DSH_HOME/cordis.patch.yml`——该层在 `--patch` 层之前合成，因此启动器那个内容相同的覆盖层是叠在它上面、幂等的，不会冲突。采集模式的日志无论如何仍是按次启动的，因为一个根目录只承载一种编码。`dump` 就是用来确认你即将拿到哪一种的。
- **`normal` 与 `trace` 需要 `pnpm run build:web`。** `batch` 只需要 `build:lib`。启动器不检查产物新鲜度，因此过期的前端包会一直提供旧的浏览器代码直到重新构建。
- **观察器是轮询的。** 没有文件系统事件订阅，也没有背压；在持久化层的写入合并窗口内，实时顺序是近似的。
- **`dsh-trace.py` 不解码 `.jsonl.zstd`。** 指向标准根目录时它会明确报错并停止，而不是读一半。请用 `trace`/`batch` 采集，或先解压。
- **trace 根目录会无限增长。** 没有任何东西修剪 `$DSH_TRACE_ROOT`；一次运行会保留它开启过的每一个 session，子智能体的也算。
