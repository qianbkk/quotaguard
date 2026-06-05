# QuotaGuard

> **Run long-running AI agents (Claude Code, Codex, Cursor, etc.) against MiniMax's 5-hour rolling quota, hands-off. Auto-pause when low, auto-resume when the window rolls over.**

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-end--to--end%20%2B%20unit-green.svg)](#testing)

---

## What it solves

If you've ever started a long task in Claude Code, walked away, and come back to find the agent has blown through its 5-hour quota mid-task — this tool is for you.

**QuotaGuard watches your MiniMax quota in the background and:**

1. **Monitors** your remaining 5h quota via a low-cost account-management endpoint (does **not** consume your inference tokens).
2. **Detects** when the bucket is nearly empty (configurable threshold, e.g. 5%).
3. **Interrupts** the running agent immediately (PreToolUse hook for Claude Code; 429 response for other agents via a local proxy).
4. **Waits** for the 5h window to roll over, using a *real-refresh* check (end-time jump) to avoid false positives.
5. **Resumes** the agent automatically with `claude --continue`, injecting `PROGRESS.md` so work continues seamlessly.
6. **Repeats** indefinitely until the task completes.

No human intervention required.

---

## Architecture (4 layers)

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: Monitor (quota_guard.monitor)                          │
│    - Adaptive polling: gradient (by %) + rate (by burn_rate)    │
│    - Real-refresh detection (end_time jump = only reliable sig) │
│    - Writes: quota_state.json (structured) + PAUSE.flag (sigil) │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2: Interceptor                                            │
│    Plan A: PreToolUse hook (Claude Code) → exit 2 = hard block  │
│    Plan B: Local proxy 127.0.0.1:8080 → 429 (Codex, Cursor, …)  │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3: Resume (quota_guard.resume)                            │
│    - Smart wait: uses window_end_at to time polling precisely   │
│    - Restarts command with --continue, injects PROGRESS.md       │
├─────────────────────────────────────────────────────────────────┤
│  Layer 4: User interface                                         │
│    - start_quotaguard.bat / .sh                                  │
│    - python -m quota_guard {status|check|monitor|proxy|resume}  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Installation

### Requirements

- Python 3.9+
- A MiniMax subscription key (`sk-cp-…`) — [MiniMax dashboard](https://platform.minimaxi.com)
- (Optional) Claude Code installed for hook-based interruption
- (Optional) `fastapi` + `uvicorn` only for the local proxy (not needed if you only use Claude Code)

### Install

```bash
git clone https://github.com/YOUR_USER/quotaguard.git
cd quotaguard
pip install requests python-dotenv
# Optional (for proxy):
pip install fastapi uvicorn
```

### Configure

```bash
cp .env.example .env
# Edit .env and set your real key:
#   MINIMAX_API_KEY=sk-cp-your-real-key
```

`.env` is git-ignored and will never be committed.

---

## Quick start

### Option A: One-click launcher (Windows)

```cmd
start_quotaguard.bat
```

Pick a mode:
- **1** — Full (monitor + resume, runs Claude Code) ← recommended
- **2** — Full + local proxy (multi-agent)
- **3** — Monitor only (just writes the PAUSE flag)
- **4** — Print current state
- **5** — Install Claude Code hooks to `.claude/`

### Option B: Manual commands

```bash
# Terminal 1: monitor + resume (orchestrator)
python -m quota_guard resume \
  --monitor-cmd "python -m quota_guard monitor --low 15 --critical 5" \
  --claude-cmd "claude --continue" \
  --clean-on-start

# Terminal 2: your long task — just talk to Claude Code normally
# When quota drops, the hook blocks new tool calls
# When the window rolls over, Claude is auto-restarted
```

### Option C: Other agents (Codex, Cursor, MiniMax Agent, …)

```bash
# Start the local proxy
python -m quota_guard proxy --port 8080

# Configure your agent to route through it
export ANTHROPIC_BASE_URL=http://127.0.0.1:8080
# or for OpenAI-compatible:
export OPENAI_BASE_URL=http://127.0.0.1:8080/v1

# When quota drops, requests receive 429 with Retry-After
# When the window rolls over, requests pass through normally
```

---

## How "real refresh" detection works

Simply watching `current_interval_remaining_percent` is unreliable — the API can briefly spike back to 100% during backend cache flushes, node switches, or boost-rounding edge cases.

QuotaGuard uses the **only signal that can't be faked**: the window's `end_time` field. When the 5h bucket rolls over, `end_time` jumps forward by exactly 5h (within 60s of clock drift). Until that jump is observed, no resume signal is emitted — even if the percentage looks healthy.

```
Before: end_time=1780592513901, pct=2%   → STOP emitted, save end_time
Probe:  end_time=1780592513901, pct=100% → still STOP (no jump)
Probe:  end_time=1780610641013, pct=100% → REAL REFRESH, clear STOP
         (delta = 18127112 ms ≈ 5h)
```

---

## Adaptive polling strategy

Two independent strategies, **the shorter interval wins**:

| Strategy | Logic | Why |
|---|---|---|
| **Gradient** (by %) | >70%: 5min / >40%: 2min / >20%: 1min / >10%: 20s / ≤10%: 5s | Simple, predictable |
| **Rate** (by burn_rate) | burn_rate from EMA on last 5 samples; if time-to-empty < 10min, switch to 5s | Anticipates fast drains |

This means a healthy 100% bucket is checked every 5 minutes (12 requests/hour ≈ 0 token cost), while a bucket predicted to empty in 2 minutes is checked every 5 seconds.

The endpoint being polled is the **account-management** API — it does **not** consume your inference tokens. Verified in production: total monitoring cost over a 24-hour period is ~144 KB of HTTP traffic, vs. tens of GB of inference quota.

---

## File layout (what gets committed)

```
quotaguard/
├── README.md
├── LICENSE
├── .gitignore                  # .env, __pycache__, .quotaguard/, PROGRESS.md
├── .env.example                # template only
├── start_quotaguard.bat        # Windows launcher
├── quota_guard/                # core package
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── state.py                # QuotaState + StateFile (atomic JSON write)
│   ├── monitor.py              # adaptive polling daemon
│   ├── interceptor.py          # PreToolUse hook entrypoint
│   ├── proxy.py                # local proxy (FastAPI)
│   ├── resume.py               # orchestrator
│   └── providers/
│       ├── __init__.py
│       └── minimax.py          # MiniMax adapter
├── hooks/                      # Claude Code hooks (copy to .claude/hooks/)
│   ├── pretool_pause_check.py
│   ├── sessionstart_inject.py
│   ├── stop_log_progress.py
│   ├── settings.json
│   └── install_hooks.bat
└── tests/
    └── e2e/                    # end-to-end simulation
        ├── fake_minimax.py
        ├── fake_signal_emitter.py
        └── fake_claude.py
```

**Not committed** (gitignored): `.env`, `__pycache__/`, `.quotaguard/`, `PROGRESS.md`, `BREAKPOINT.md`, IDE/OS files.

---

## State file format

`quota_state.json` is the structured source of truth:

```json
{
  "remains_pct": 28.5,
  "remains_time_ms": 1200000,
  "weekly_pct": 100.0,
  "boost": 1.0,
  "model_name": "general",
  "low_threshold": 15.0,
  "critical_threshold": 5.0,
  "is_low": false,
  "is_critical": false,
  "burn_rate_per_min": 0.42,
  "estimated_empty_at": 1749876543.0,
  "samples": [{"at": 1749876000.0, "pct": 30.0, "is_critical": false}],
  "window_start_at": 1749858900.0,
  "window_end_at": 1749876900.0,
  "end_time_ms": 1780592513901,
  "last_check_at": 1749876300.0,
  "consecutive_low_count": 0,
  "consecutive_critical_count": 0,
  "last_end_time_before_stop": 1780574513901,
  "refresh_confirmed": false
}
```

This file is read by the hook, the proxy, and the orchestrator. It's also the easiest way to give an AI agent a structured read of the current state.

---

## How Claude Code integrates (PreToolUse hook)

When the hook fires (before every tool call), it does:

1. Read `quota_state.json` (cheap, ~50 bytes)
2. If `is_critical`: write to stderr, **exit 2** — Claude Code blocks the tool call
3. Otherwise: exit 0, tool call proceeds

`exit 2` is a **hard block** — even `--dangerously-skip-permissions` cannot bypass it. When Claude sees the stderr message, it knows to:
1. Read `BREAKPOINT.md` for the auto-saved checkpoint
2. Write a short note to `PROGRESS.md` describing what it just did
3. Type `/exit` to terminate cleanly

The orchestrator detects the exit, watches for `RESUME.flag` (written by the monitor after a real end-time jump), then restarts Claude with `claude --continue`. A `SessionStart` hook re-injects `PROGRESS.md` into the new session's context.

---

## Testing

The `tests/e2e/` directory contains three scripts that simulate the full loop without touching the real API:

- `fake_minimax.py` — local HTTP server mimicking the MiniMax endpoint, with controlled decline and window-end jumps
- `fake_signal_emitter.py` — emulates monitor's STOP/RESUME behavior on a schedule
- `fake_claude.py` — emulates Claude Code's tool-loop, reading PROGRESS.md and honoring PAUSE.flag

Run an end-to-end test:

```bash
# In one terminal
python -m quota_guard resume \
  --monitor-cmd "python tests/e2e/fake_signal_emitter.py --base-dir {project_dir}/.quotaguard --pause-after 3 --resume-after 5 --max-cycles 5" \
  --initial-cmd "python tests/e2e/fake_claude.py --tool-sleep 3 --rounds 10" \
  --claude-cmd "python tests/e2e/fake_claude.py --tool-sleep 3 --rounds 10" \
  --clean-on-start
```

You should see 5 rounds, each ending with the fake Claude seeing PAUSE.flag, /exit, and the next round reading a longer PROGRESS.md.

---

## Configuration reference

### CLI flags (monitor)

| Flag | Default | Description |
|---|---|---|
| `--low` | 15.0 | Warning threshold (%) — speeds up polling |
| `--critical` | 5.0 | Hard-stop threshold (%) — writes PAUSE.flag |
| `--state-file` | `./.quotaguard/quota_state.json` | Structured state path |
| `--pause-file` | `./.quotaguard/PAUSE.flag` | Sentinel file |
| `--resume-file` | `./.quotaguard/RESUME.flag` | Refresh signal file |

### Environment variables

| Var | Default | Description |
|---|---|---|
| `MINIMAX_API_KEY` | (required) | Your MiniMax subscription key |
| `QUOTAGUARD_STATE` | `~/.quotaguard/quota_state.json` | Override state-file location |
| `QUOTAGUARD_PAUSE` | `~/.quotaguard/PAUSE.flag` | Override pause-file location |

---

## How is this different from other tools?

| Project | Polling | Pause | Real refresh | Auto-resume | Multi-agent |
|---|---|---|---|---|---|
| One-shot scripts (`minimax_quick.py`) | Manual | ❌ | ❌ | ❌ | ❌ |
| Provider-agnostic libs (PESS-style) | ✅ | Signal file | ❌ | ❌ | Engine only |
| **QuotaGuard** | ✅ adaptive | Hook + Proxy | ✅ end-time | ✅ Claude continue | ✅ |

The two layers most projects skip — **real-refresh detection** and **multi-agent pause** — are exactly what makes QuotaGuard safe to leave running unattended.

---

## Known limitations

- The MiniMax API's `status_code` 2056 ("5h window exhausted") is detected but currently treated identically to 100% (we still wait for the end-time jump before resuming). This is conservative.
- The PreToolUse hook is per-invocation, so a single in-flight long tool call (e.g. a 5-minute `npm install`) cannot be interrupted. The next call will be blocked.
- The proxy adds ~1ms latency to every request. If you measure sub-millisecond SLAs, run the agent directly.
- The `--continue` flag on Claude Code relies on its session index, which has a known bug ([anthropics/claude-code#33912](https://github.com/anthropics/claude-code/issues/33912)). Workaround: use `/resume` interactively if `--continue` fails to find the previous session.

---

## Contributing

Issues and PRs welcome. Please:
- Do not commit any `.env` or real API keys
- Run the e2e test before submitting a PR
- Add a test under `tests/` for any new behavior

---

## License

MIT. See [LICENSE](LICENSE).
