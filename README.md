# model-effort-router

**Pick the right Claude model and effort for every task and subagent, automatically, and check the rules on real tasks.**

A [Claude Code](https://code.claude.com) plugin. Before a subagent or worker starts, it reads the task (a story, a GitHub issue or a plain prompt), the labels people put on it and the repository it touches, and chooses **Sonnet or Opus** and an **effort level** (medium, high, xhigh). A public [SWE-bench Verified](https://www.swebench.com/) benchmark in this repository shows what that choice costs and solves compared with simpler rules.

In other words: an explainable **LLM router** (model routing) for Claude Code subagents and agent teams, to cut **token cost** without losing solved tasks, and a measured answer to **Sonnet vs Opus** and **which effort level** to use.

[![test](https://github.com/islamkmansoori/model-effort-router/actions/workflows/test.yml/badge.svg)](https://github.com/islamkmansoori/model-effort-router/actions/workflows/test.yml)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

- **Automatic.** A hook sets `model` and `effort` on every subagent Claude starts (checked live: the subagent ran on the model the hook chose).
- **Explainable.** Every choice comes with its reasons: `opus, effort high. Score 3: size M (+2); open question (+1); risk money (line): at least opus, high`.
- **Words never raise your bill.** A risk or a critical part that is only guessed from wording is named, not acted on; explicit lines, labels and the paths you configure decide.
- **Knows why a worker failed.** It reads the failing output and tells a wrong change from a broken environment or an unclear task. Only the first earns a stronger model; the same failure on two models stops the climb; after two retries it says to ask a person.
- **Checks the task before spending.** Vague acceptance criteria and open questions are flagged first: an unclear task fails on every model.
- **Spend control.** Daily, weekly and monthly budgets with alerts, an optional cap in the hook, and an audit of what the hook actually chose.
- **Measured.** An outcome log suggests better rules from your own results; the benchmark publishes every run.
- **No dependencies.** Standard-library Python 3.9+, one config file, MIT.

## Install

In Claude Code:

```
/plugin marketplace add islamkmansoori/model-effort-router
/plugin install model-effort-router@model-effort-router
```

That's it: subagents are now routed. To route a task by hand, ask Claude to "route this task" or type `/route-task <file | owner/repo#123 | "prompt">`.

Without the plugin, copy `scripts/router.py` and `config/default.json` (as `scripts/` and `config/`) into a project and run `python3 scripts/router.py --help`.

## What the benchmark found

21 real GitHub issues from [SWE-bench Verified](https://www.swebench.com/) (pytest and pylint), each run on all four rungs with Claude Code: 84 runs, every one published.

1. **Start cheap and jump straight to Opus on failure.** Sonnet medium first, Opus high if it fails: 20 of 21 solved for **$4.01**, against **$4.62** for always using Opus high (same tasks solved, 13% less). It also won on the half of the tasks that no tuning saw. This is now the default (`"retry_jumps_model": true`).
2. **The in-between rungs rarely pay.** More effort on Sonnet rescued 1 task out of 7 that Sonnet medium failed; Opus xhigh solved nothing Opus high did not, for more money.
3. **Reading a raw bug report does not predict its difficulty well.** Neither the word rules nor the Haiku assessor picked out the hard issues in advance, so on plain issues the router starts on the cheap rung and the jump does the rest. The router earns its place with explicit facts (`**Critical:**`, `**Risk:**`, labels, paths), where a wrong first choice is expensive, not by guessing difficulty from prose.
4. **Keyword tiers do not predict it either.** A four-tier keyword scorer from an earlier project (interface and CRUD words pull a task down; architecture, algorithm and DevOps words push it up) was run against the same tasks. Starting its top tier on Opus cost **$4.21**, more than starting everything cheap ($4.01). It is kept in `bench/keyword_tiers.py` so you can try your own word list against the same runs.
5. **A guess that acts costs money.** One task was sent to Opus xhigh because its pasted package list contained the word "cryptography". Sonnet medium solved it. Since then a critical part guessed from wording is only named, like a guessed risk.
6. **There is room left.** Knowing in advance which rung would work costs **$2.70** for the same 20 tasks: a better predictor could still save about a third. Tell us what predicts it in your tasks.

Caveats: one run per task and rung (models vary run to run), bug fixes in two Python libraries, costs are Claude Code's own estimates, and "failure" here is the hidden tests failing; in real work your worker's own tests and review have to catch it.

## On a real project

On a private 18-day multi-agent build (43 sessions, 19,226 model replies), mixing models saved an estimated **29%** against running everything on Opus 5.5: about $1,586 instead of $2,242 in API-equivalent dollars. That measures model choice in general, most of it made by hand before the router existed; it is the prize the router automates. Details and method: [docs/case-study.md](docs/case-study.md). Measure your own with `router.py savings`.

## Benchmark results

<!-- results:start -->
21 SWE-bench Verified tasks, each run once on every rung with Claude Code in headless mode (`--bare` or `--safe-mode`: no user customizations; per-run budget cap). Solved = every hidden test passes. Cost = Claude Code's own `total_cost_usd` estimate. One run per task and rung: small numbers, read them as a pilot.

| Way of choosing | Tasks solved | Total cost | Cost per solved task |
|---|---|---|---|
| Always sonnet medium | 14/21 | $1.62 | $0.12 |
| Always sonnet high | 15/21 | $1.78 | $0.12 |
| Always opus high | 20/21 | $4.62 | $0.23 |
| Always opus xhigh | 20/21 | $5.50 | $0.28 |
| Start at the bottom, climb on failure | 20/21 | $4.38 | $0.22 |
| Start at the bottom, jump to the next model on failure | 20/21 | $4.01 | $0.20 |
| Router (rules only) | 14/21 | $1.62 | $0.12 |
| Router (rules only), jump on failure | 20/21 | $4.01 | $0.20 |
| Router (with assessor) | 14/21 | $1.69 | $0.12 |
| Router (with assessor), jump on failure | 20/21 | $4.08 | $0.20 |
| Keyword tiers (top tier starts on Opus) | 17/21 | $2.87 | $0.17 |
| Keyword tiers (top tier starts on Opus), jump on failure | 20/21 | $4.21 | $0.21 |
| Cheapest rung that worked (hindsight) | 20/21 | $2.70 | $0.13 |

Router costs include the assessor's own model call. Climb on failure = if the chosen rung fails, run the next one up, paying for every attempt; jump = go straight to the next model. Keyword tiers is a four-tier keyword scorer from an earlier project (`bench/keyword_tiers.py`).

**Tuned on one half, tested on the other.**

The 21 tasks are split in a fixed way (every other task by id). 24 starting rules and climbing styles were tried on the tuning half only; the best is scored here on the test half, which it never saw. Chosen: router with the assessor, thresholds [[2, 2], [0, 0]] (points to rung), jump to the next model on failure.

| Way of choosing (test half) | Tasks solved | Total cost | Cost per solved task |
|---|---|---|---|
| Always sonnet medium | 8/10 | $0.68 | $0.08 |
| Always sonnet high | 8/10 | $0.75 | $0.09 |
| Always opus high | 10/10 | $1.87 | $0.19 |
| Always opus xhigh | 10/10 | $2.36 | $0.24 |
| Start at the bottom, climb on failure | 10/10 | $1.35 | $0.14 |
| Start at the bottom, jump to the next model on failure | 10/10 | $1.20 | $0.12 |
| Router (default rules) | 8/10 | $0.68 | $0.08 |
| Router (default rules), jump on failure | 10/10 | $1.20 | $0.12 |
| Cheapest rung that worked (hindsight) | 10/10 | $1.07 | $0.11 |
| **Tuned router (chosen on the other half)** | 10/10 | $1.23 | $0.12 |

Per task, with every run's cost: [bench/results/REPORT.md](bench/results/REPORT.md).
<!-- results:end -->

How it was measured, and how to rerun it: [bench/README.md](bench/README.md). Every run is in [bench/results/runs.jsonl](bench/results/runs.jsonl).

## How it decides

| What the task says | Model and effort |
|---|---|
| `**Critical:** yes`, a `critical` label, or a file under a configured critical path | opus, xhigh (always) |
| `**Risk:** money`, `security` or `data`, a mapped label, or a configured risk path | at least opus, high |
| 9 points or more | opus, high |
| 6 to 8 points | sonnet, high |
| 5 points or less | sonnet, medium |

**Points:** size (XS 0, S 1, M 2, L 4, XL 6; from the text, a `size: L` label, or the optional assessor), many acceptance criteria (+1 or +2), many dependencies (+1), a database schema, permissions or personal data, a user interface, an open question, shared or build files, concurrency or performance (+1 each), and from the repository: many touched files (+1 or +2), touched code without tests (+1), often-changed files (+1).

**Words only guess.** A task often mentions a critical part to say it does *not* change. Sentences with "no", "not", "never" or "unchanged" are skipped, and a risk or a critical part found only in words is named ("the text may touch the critical part") but does not move the choice.

**Before starting.** Every score also says whether the task is ready: *2 of 5 criteria are vague (correctly, properly)*, *an open question: a person has to answer it first*, or *no acceptance criteria*. This is advice; it never changes the model.

**Retries.** A retry moves up only for the worker's own failure, and by default it jumps straight to the next model (Sonnet to Opus high), because that was cheaper in the benchmark than more effort on the same model (`"retry_jumps_model": false` climbs one rung at a time).
- `--retry 1 --cause worker|environment|task` says whose failure it was. An environment or task failure earns no step: a bigger model fixes neither.
- `--retry 1 --failure-log out.txt` suggests the cause from the failing output instead. Environment evidence (a missing module, a full disk, a refused connection, a rate limit) wins over task evidence, which wins over a failed assertion: a model is blamed only when nothing else explains the failure. `router.py cause out.txt` shows the suggestion and its evidence on its own.
- The same failure on two different models earns no further step: it is unlikely to be the worker.
- After `max_worker_retries` (2) it stops climbing and says to ask a person.

**The assessor (optional).** When a task says little about its size (a short issue, a one-line prompt), `--assess` asks a cheap model (Haiku, low effort, read-only) to read the task and the code and estimate the size and the files likely to change; `--assess auto` asks only when the task does not state its size. It cost under a cent per task in the benchmark.

**Budget (optional).** Set `budget` limits in the config: `router.py budget` reports today's, the week's and the month's API-equivalent spend from Claude Code's own usage records, with alerts at 75%, 90% and 100%, and exits 1 when a limit is passed. With `"throttle": true`, the hook caps first attempts of ordinary tasks at the cheap rung once today's spend passes 80% of the daily limit; critical and risky tasks, and retries, are never capped.

**Mechanical tasks (optional, off by default).** With `"mechanical": {"enabled": true}`, a short task that only runs a tool (run the tests, lint, `git status`) goes to Haiku, unless it asks for a change. This is not benchmarked; switch it on if it fits your work.

## Command line

```
python3 scripts/router.py score TASK.md --repo . --write        # writes the choice under the task's title
python3 scripts/router.py score --issue owner/repo#123 --repo .  # a GitHub issue (GITHUB_TOKEN for private ones)
python3 scripts/router.py score --text "Fix the login timeout" --repo . --assess auto
python3 scripts/router.py score TASK.md --write --retry 1 --cause worker
python3 scripts/router.py score TASK.md --write --retry 1 --failure-log out.txt   # the cause is suggested from the output
python3 scripts/router.py cause out.txt                        # whose failure: worker, environment or task?
python3 scripts/router.py check-env                            # anything that would override the choice?
python3 scripts/router.py audit                                # what the hook did: routed, kept, skipped, capped
python3 scripts/router.py budget                               # spend against your daily, weekly, monthly limits
python3 scripts/router.py record TASK.md --ran sonnet/high --first-try pass --findings 0
python3 scripts/router.py summary                              # outcomes, with suggested point changes
python3 scripts/router.py savings --since 2026-11-01            # what model choice saved, from Claude Code's usage records
python3 scripts/router.py init                                 # .claude/model-router.json to edit
```

On Windows use `python` instead of `python3`.

## Configure

`init` writes `.claude/model-router.json`; a key you set replaces the default. Everything is there: the ladder (add `["haiku", "low"]` at the bottom or end with `["opus", "max"]`), the thresholds, size and criteria patterns, signals, labels, critical and risk **paths** (globs such as `src/billing/*`), denial words, the assessor, the hook (`enabled`, `skip_agent_types`, `respect_explicit`, `assess`, `runs_log`), the retry limit, the readiness words, the failure-cause patterns, the budget, the mechanical rung and the model prices.

## Learn from your own outcomes

`record` adds one row per finished task to `docs/model-router-log.md`: score, signals, what was chosen, what actually ran, first-try pass, worker retries, other failures, review findings, rework. `summary` groups them and suggests changes, for example *raise 'user interface' (now 1): its tasks had trouble 4/6 times against 25% overall*, or *sonnet/medium went cleanly on all 7 tasks: try sending more to it*. Change the rules after 10 to 20 tasks, not before.

## FAQ

**Which Claude model should a subagent use?**
On our benchmark of real bug fixes: start on Sonnet (medium effort) and switch to Opus (high effort) if it fails. Sonnet medium solved 14 of 21 tasks at about $0.08 a run; Opus high solved 20 of 21 at about $0.22 a run; starting on Sonnet and jumping to Opus on failure solved 20 for 13% less than always using Opus. Mark tasks that touch money, security, stored data or your system's critical part, and the router starts those on Opus directly.

**How do I set the model and effort of a Claude Code subagent?**
Per call, with the Agent tool's `model` and `effort` parameters; per subagent, with `model:` and `effort:` in its frontmatter; for a session, `claude --model opus --effort high`. This plugin's hook fills the Agent tool's parameters for you.

**Why does my subagent ignore the effort I set?**
`CLAUDE_CODE_EFFORT_LEVEL` beats every other effort setting, including the Agent tool's `effort` and a subagent's `effort:`. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` makes subagents ignore the model they are given. `python3 scripts/router.py check-env` names both.

**My agent failed. Is it the model, the environment or the task?**
`python3 scripts/router.py cause out.txt` reads the failing output and says which, with the lines it relied on. A missing module, a full disk, a refused connection or a rate limit is the environment; "ambiguous" or "needs a decision" is the task; a failed assertion with nothing else wrong is the worker. Only the last is worth a stronger model.

**How do I cap what Claude Code spends on subagents?**
Set `"budget": {"daily": 20, "throttle": true}` in `.claude/model-router.json`. `router.py budget` reports spend against the limit, and past 80% of it the hook starts ordinary subagents on the cheap rung. Critical and risky tasks are never capped.

**How do I know the router is really choosing my subagents' models?**
`python3 scripts/router.py audit` reads the hook's log: how many subagent starts it routed, how many kept a model the caller set, how many were skipped, and whether a machine setting overrode the choice.

**Is `xhigh` available on every model?**
No. On a model without it, Claude Code quietly runs the highest level it supports (for example `high`).

**Is climbing from the cheapest model up the cheapest way?**
Climbing one rung at a time was not: the failed attempts on Sonnet high cost more than they saved. Jumping from Sonnet medium straight to Opus high was the cheapest approach that solved everything Opus did. Is Opus xhigh worth it over Opus high? Not on these tasks.

**How is this different from other routers?**
Most routers (for example claude-model-router, Jev-Router, RouteLLM) judge difficulty from the wording of a prompt, or learn it from preference data. This one reads the task's own facts and the repository, keeps risk separate from size, explains every choice, and publishes a benchmark you can rerun. Those tools route in ways this one does not (per turn, learned routers); pick what fits.

**Does it send my code anywhere?**
No. The router and the hook run locally. The only network calls are `--issue` (GitHub's API) and `--assess` (one Claude call, through your own Claude Code), and both happen only when you ask for them.

**Can it use Haiku?**
Yes: add `["haiku", "low"]` to the bottom of the ladder. The assessor uses Haiku by default.

## Limits

- The default points and thresholds are a starting rule. The benchmark covers bug fixes in open-source Python libraries; your tasks may differ, which is why the outcome log exists.
- Word patterns are crude. Prefer the explicit `**Critical:**` and `**Risk:**` lines, labels and paths.
- Passing tests are evidence, not proof. For critical and risky tasks, keep a review.
- Model names and effort levels follow Claude Code as of November 2026.

## Background

The mechanisms around the routing (failure causes, the repeated-failure guard, the retry limit, the readiness check, budget alerts and the audit) come from an earlier agent-orchestration project by the same author, rebuilt here without dependencies. Its keyword-tier scorer was tested against the benchmark and is kept as a baseline, not as a rule.

## Contributing

`python3 -I tests/test_router.py` must print `0 failed`, and `claude plugin validate . --strict` must pass. See [AGENTS.md](AGENTS.md) and [CHANGELOG.md](CHANGELOG.md).

## Cite

See [CITATION.cff](CITATION.cff), or use GitHub's "Cite this repository" button.

## License

MIT. See [LICENSE](LICENSE).
