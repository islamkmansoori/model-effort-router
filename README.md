# model-effort-router

A Claude Code plugin that chooses the **model** (Sonnet or Opus) and **effort** (medium, high, xhigh) for each task before you hand it to a subagent or a separate worker session, and keeps a record of how each task went, so the rules can be tuned from results instead of by feel.

Most routers guess how hard a task is from the wording of a prompt. This one reads the **task file** (a story, an issue, a work packet), where the facts that make a task hard are written down: its size, its acceptance criteria, its dependencies, whether it touches the database, permissions or a screen, and whether a question is still open.

Standard library Python only. No package to install, no network calls.

## Install

As a plugin (in Claude Code):

```
/plugin marketplace add <owner>/model-effort-router
/plugin install model-effort-router@model-effort-router
```

Then ask Claude to "route this task" or type `/route-task <task file>`.

Without the plugin: copy `scripts/router.py` and `config/default.json` into your project (keep them side by side, as `scripts/` and `config/`) and run the script yourself.

## Use

```
python3 scripts/router.py init                      # .claude/model-router.json: your project's rules
python3 scripts/router.py check-env                 # anything on this machine that would override the choice?
python3 scripts/router.py score TASK.md --write     # writes the choice under the task's title
python3 scripts/router.py score TASK.md --write --retry 1 --cause worker
python3 scripts/router.py record TASK.md --ran sonnet/high --first-try pass --findings 0
python3 scripts/router.py summary
```

On Windows, use `python` instead of `python3`.

## How it decides

| In the task file | Model and effort |
|---|---|
| `**Critical:** yes` (the part of your system where a mistake costs most) | opus, xhigh (always) |
| `**Risk:** money`, `security` or `data` | at least opus, high |
| 9 points or more | opus, high |
| 6 to 8 points | sonnet, high |
| 5 points or less | sonnet, medium |

**Points** come from: size (XS 0, S 1, M 2, L 4, XL 6), many acceptance criteria (7+ adds 1, 10+ adds 2), more than 4 dependencies (+1), and one point each for database schema, permissions or personal data, a user interface, an open question, shared or build files, and concurrency or performance.

Four rules that come from an independent review of the first version:

1. **Explicit lines decide, words only guess.** A task often mentions a critical part to say it does *not* change. Without a `**Critical:**` line the tool guesses, skips sentences that deny the match ("no", "not", "never", "unchanged"), and labels the result "guessed".
2. **Risk is separate from size.** A one-line change to a price calculation can do more harm than a large screen. A risk the tool only guesses from words is *named*, never acted on, so loose word matches can't raise your cost.
3. **Only the worker's own failures earn a stronger model.** `--retry` needs `--cause`: `worker` climbs one rung, while `environment` (broken build, missing files, ports) or `task` (it needs a decision, or its criteria contradict each other) stays put. A bigger model fixes neither.
4. **Check what actually ran.** `check-env` names the settings that silently override a choice. `CLAUDE_CODE_EFFORT_LEVEL` beats every other effort setting, `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` makes subagents ignore the model they are given, and `ANTHROPIC_MODEL` sets a session's model when it is started without `--model`. `record --ran` compares what ran with what was chosen and warns when they differ.

## Tune it from outcomes

`record` writes one row per finished task to `docs/model-router-log.md`: score, signals, chosen, ran, first-try pass, worker retries, other failures, review findings, rework. `summary` groups them by choice and by signal. When the tasks with a given signal fail more often, give that signal more points. When a signal predicts nothing, take points away. Wait for 10 to 20 tasks before changing anything.

## Configure

`init` copies `config/default.json` to `.claude/model-router.json`. A project config replaces only the keys it names. Everything is there: the ladder (for example add `["haiku", "low"]` at the bottom, or end with `["opus", "max"]`), the point thresholds, the size and criteria patterns, the signals, the critical-part guess, the risk kinds and floor, the denial words, lines to skip (for example a rules paragraph every task carries), and the log path.

## Limits, honestly

- The default points and thresholds are a **starting rule**. Nothing has proven they pick the cheapest model that succeeds; that is what the log is for.
- Word patterns are crude. Prefer the explicit `**Critical:**` and `**Risk:**` lines.
- Passing tests are evidence, not proof. For critical and risky tasks, keep a separate review.
- Model names and effort levels follow Claude Code as of November 2026. `xhigh` quietly falls back to `high` on models that don't support it.

## Test

```
python3 -I tests/test_router.py
```

## License

MIT. See [LICENSE](LICENSE).
