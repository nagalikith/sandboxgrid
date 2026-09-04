# Trace histogram

Turns: 3. source={'synthetic-shape': 3}.

**synthetic-shape — not a Claude Code dump.** cached_tokens were not invented.
Do not quote this file as live agent usage.

- prompt_tokens: n=3 min=50 p50=73 p90=85 max=85 mean=69
- cached_fraction: n=3 mean=0.000 max=0.000
- user_message_count: n=3 min=1 p50=1 p90=2 max=2 mean=1
- tool_count: n=3 min=0 p50=0 p90=1 max=1 mean=0
- estimated context budget (prompt + thinking + max_tokens): n=3 min=8242 p50=8265 p90=8277 max=8277 mean=8261
- rows with tools: 1 / 3
- rows with ≥2 user messages: 1 / 3
- rows with a tool loop: 1 / 3
- agent-shaped (tools or multi-turn or tool loop): 3 / 3
- budget over Kimi 262K: 0 / 3

Budget vs windows:

| window | count |
|---|---:|
| kimi-262K | 3 |
| minimax-512K | 0 |
| glm-1M | 0 |
| over_1M | 0 |

Policy note from this shape: coding-agent rows are not short no-tool
MiniMax turns. Context cutover is a budget against 262K, not pad-spam.
Stay on the hot prefix — Kimi cached input is $0.19 vs $0.95.
