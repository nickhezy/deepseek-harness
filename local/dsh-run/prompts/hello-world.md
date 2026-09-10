Multi-agent hello world. Do NOT write the greetings yourself — every one of them must come from a subagent.

Issue three `subagent` calls in a single assistant message so the children run side by side. Each child gets exactly one job, must answer from its own knowledge, must call no tools, and must reply with the greeting sentence only:

1. one short greeting in English, starting with 'Hello'
2. one short greeting in Chinese, starting with '你好'
3. one short greeting in French, starting with 'Bonjour'

When all three have returned, print exactly this and nothing else:

1. English: <child 1 reply>
2. Chinese: <child 2 reply>
3. French: <child 3 reply>
subagents: 3
