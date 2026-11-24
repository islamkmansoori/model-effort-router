# Sonnet or Opus for each Claude Code subagent? I ran 84 tasks to find out

*Draft for a blog post, dev.to or Medium. Keep the numbers; shorten the rest as you like.*

Claude Code lets every subagent run on its own model and effort level. Opus is stronger and costs more; Sonnet is cheaper. Most "routers" guess which one a task needs from the wording of the prompt. I wanted to know what actually works, so I built a small open-source plugin, [model-effort-router](https://github.com/islamkmansoori/model-effort-router), and measured it.

## The test

21 real GitHub issues from SWE-bench Verified (pytest and pylint), each with hidden tests that decide whether the fix is right. Every issue ran on four settings: Sonnet medium, Sonnet high, Opus high and Opus xhigh. That is 84 runs, all published with their costs.

## What I found

| Way of choosing | Solved | Cost |
|---|---|---|
| Always Sonnet medium | 14/21 | $1.62 |
| Always Opus high | 20/21 | $4.62 |
| Always Opus xhigh | 20/21 | $5.50 |
| Climb one setting at a time on failure | 20/21 | $4.38 |
| **Sonnet medium, jump to Opus high on failure** | **20/21** | **$4.01** |
| Perfect hindsight (the floor) | 20/21 | $2.70 |

1. **Start cheap, jump straight to Opus on failure.** Same tasks solved as always-Opus, 13% cheaper. It also won on a held-out half of the tasks.
2. **The in-between settings rarely pay.** Sonnet with more effort rescued 1 of 7 failures. Opus xhigh solved nothing Opus high did not.
3. **The issue text did not predict difficulty.** Neither keyword rules nor a cheap Haiku "assessor" spotted the hard issues in advance. What does help is stating facts: mark a task as touching money, security, stored data or your system's critical part, and start it on Opus.
4. **A third is still on the table.** Hindsight would have cost $2.70. If you know what predicts difficulty in your tasks, the plugin's outcome log is built to find it.

## The plugin

Two commands in Claude Code:

```
/plugin marketplace add islamkmansoori/model-effort-router
/plugin install model-effort-router@model-effort-router
```

A hook then sets the model and effort of every subagent, explains each choice, never raises cost on a guess, only escalates when the worker itself failed, and keeps a log that suggests better rules from your own results. Standard-library Python, MIT. One more thing I learned on the way: if your subagents ignore the effort you set, look for `CLAUDE_CODE_EFFORT_LEVEL`. It overrides everything else.

Caveats: one run per task and setting, two Python libraries, costs are Claude Code's own estimates. The benchmark is in the repository: rerun it, add tasks, prove me wrong.
