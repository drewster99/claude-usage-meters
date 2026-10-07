# usage-meters

A Claude Code mod that puts one row above the prompt:

**Context** 229k    **NCR tok** 277k    **Last hour** 84k

- **Context**: tokens in the live context window, as of the last response.
- **NCR tok**: non-cache-read tokens (uncached input + cache writes + output) this session and its subagents
  have used today, since local midnight.
- **Last hour**: the same, over the last 60 minutes.

The token counts come from the session's transcript files (main thread and `subagents/`), read incrementally by
`bin/tally_tokens.py`. They refresh after every response and every 30 seconds. A failed count shows as an error,
never as zero.

## Install

At the prompt of a Claude Code terminal session:

```
/plugin install usage-meters --marketplace drewster99/claude-usage-meters
```

Answer `y` to add the marketplace, then choose the **user** scope so it runs in every folder.

Requires macOS with `/usr/bin/python3` (Command Line Tools) and a Claude Code build with function-hook mods.
The tally cache lives in `~/Library/Caches/claude-usage-meters/`.

## Develop

```
claude plugin validate .
claude plugin test .
/usr/bin/python3 -m unittest discover -s bin
```
