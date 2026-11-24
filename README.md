# model-effort-router

**Pick the right Claude model and effort for every task and subagent, automatically, and check the rules on real tasks.**

A [Claude Code](https://code.claude.com) plugin. Before a subagent or worker starts, it reads the task (a story, a GitHub issue or a plain prompt), the labels people put on it and the repository it touches, and chooses **Sonnet or Opus** and an **effort level** (medium, high, xhigh). A public [SWE-bench Verified](https://www.swebench.com/) benchmark in this repository shows what that choice costs and solves compared with simpler rules.

[![test](https://github.com/islamkmansoori/model-effort-router/actions/workflows/test.yml/badge.svg)](https://github.com/islamkmansoori/model-effort-router/actions/workflows/test.yml)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

- **Automatic.** A hook sets `model` and `effort` on every subagent Claude starts (checked live: the subagent ran on the model the hook chose).
- **Explainable.** Every choice comes with its reasons: `opus, effort high. Score 3: size M (+2); open question (+1); risk money (line): at least opus, high`.
- **Safe by design.** Guessed risk never raises your bill; only explicit lines, labels and paths you configure do. A stronger model is tried only when the worker itself failed, not when the environment or the task was the problem.
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

## Benchmark results

<!-- results:start -->
15 SWE-bench Verified tasks, each run once on every rung with Claude Code in headless mode (`--bare` or `--safe-mode`: no user customizations; per-run budget cap). Solved = every hidden test passes. Cost = Claude Code's own `total_cost_usd` estimate. One run per task and rung: small numbers, read them as a pilot.

| Way of choosing | Tasks solved | Total cost | Cost per solved task |
|---|---|---|---|
| Always sonnet medium | 8/15 | $1.22 | $0.15 |
| Always sonnet high | 9/15 | $1.32 | $0.15 |
| Always opus high | 14/15 | $3.43 | $0.25 |
| Always opus xhigh | 14/15 | $4.22 | $0.30 |
| Start at the bottom, climb on failure | 14/15 | $3.99 | $0.28 |
| Start at the bottom, jump to the next model on failure | 14/15 | $3.61 | $0.26 |
| Router (rules only) | 8/15 | $1.22 | $0.15 |
| Router (rules only), climb on failure | 14/15 | $3.99 | $0.28 |
| Router (with assessor) | 8/15 | $1.27 | $0.16 |
| Router (with assessor), climb on failure | 14/15 | $4.04 | $0.29 |
| Cheapest rung that worked (hindsight) | 14/15 | $2.30 | $0.16 |

Router costs include the assessor's own model call. Climb on failure = if the chosen rung fails, run the next one up, paying for every attempt.

**Tuned on one half, tested on the other.**

The 15 tasks are split in a fixed way (every other task by id). 20 starting rules and climbing styles were tried on the tuning half only; the best is scored here on the test half, which it never saw. Chosen: router with the assessor, thresholds [[1, 2], [0, 1]] (points to rung), climb one rung on failure.

| Way of choosing (test half) | Tasks solved | Total cost | Cost per solved task |
|---|---|---|---|
| Always sonnet medium | 5/7 | $0.48 | $0.10 |
| Always sonnet high | 5/7 | $0.54 | $0.11 |
| Always opus high | 7/7 | $1.33 | $0.19 |
| Always opus xhigh | 7/7 | $1.71 | $0.24 |
| Start at the bottom, climb on failure | 7/7 | $1.15 | $0.16 |
| Start at the bottom, jump to the next model on failure | 7/7 | $0.99 | $0.14 |
| Router (default rules) | 5/7 | $0.48 | $0.10 |
| Router (default rules), climb on failure | 7/7 | $1.15 | $0.16 |
| Cheapest rung that worked (hindsight) | 7/7 | $0.87 | $0.12 |
| **Tuned router (chosen on the other half)** | 7/7 | $1.15 | $0.16 |

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

**Words only guess.** A task often mentions a critical part to say it does *not* change. Sentences with "no", "not", "never" or "unchanged" are skipped, guesses are labelled "guessed", and a risk found only in words is named but does not move the choice.

**Retries.** `--retry N --cause worker` climbs the ladder; `--cause environment` or `task` does not, because a bigger model fixes neither. With `"retry_jumps_model": true`, a retry goes straight to the next model instead of more effort on the same one.

**The assessor (optional).** When a task says little about its size (a short issue, a one-line prompt), `--assess` asks a cheap model (Haiku, low effort, read-only) to read the task and the code and estimate the size and the files likely to change. It cost under a cent per task in the benchmark.

## Command line

```
python3 scripts/router.py score TASK.md --repo . --write        # writes the choice under the task's title
python3 scripts/router.py score --issue owner/repo#123 --repo .  # a GitHub issue (GITHUB_TOKEN for private ones)
python3 scripts/router.py score --text "Fix the login timeout" --repo . --assess
python3 scripts/router.py score TASK.md --write --retry 1 --cause worker
python3 scripts/router.py check-env                            # anything that would override the choice?
python3 scripts/router.py record TASK.md --ran sonnet/high --first-try pass --findings 0
python3 scripts/router.py summary                              # outcomes, with suggested point changes
python3 scripts/router.py init                                 # .claude/model-router.json to edit
```

On Windows use `python` instead of `python3`.

## Configure

`init` writes `.claude/model-router.json`; a key you set replaces the default. Everything is there: the ladder (add `["haiku", "low"]` at the bottom or end with `["opus", "max"]`), the thresholds, size and criteria patterns, signals, labels, critical and risk **paths** (globs such as `src/billing/*`), denial words, the assessor, and the hook (`enabled`, `skip_agent_types`, `respect_explicit`, `assess`, `runs_log`).

## Learn from your own outcomes

`record` adds one row per finished task to `docs/model-router-log.md`: score, signals, what was chosen, what actually ran, first-try pass, worker retries, other failures, review findings, rework. `summary` groups them and suggests changes, for example *raise 'user interface' (now 1): its tasks had trouble 4/6 times against 25% overall*, or *sonnet/medium went cleanly on all 7 tasks: try sending more to it*. Change the rules after 10 to 20 tasks, not before.

## FAQ

**Which Claude model should a subagent use?**
It depends on the task, which is the point of this plugin. In our benchmark the cheaper model solved the easy tasks for a fraction of the cost, and the harder ones needed Opus; see the results above for the numbers.

**How do I set the model and effort of a Claude Code subagent?**
Per call, with the Agent tool's `model` and `effort` parameters; per subagent, with `model:` and `effort:` in its frontmatter; for a session, `claude --model opus --effort high`. This plugin's hook fills the Agent tool's parameters for you.

**Why does my subagent ignore the effort I set?**
`CLAUDE_CODE_EFFORT_LEVEL` beats every other effort setting, including the Agent tool's `effort` and a subagent's `effort:`. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` makes subagents ignore the model they are given. `python3 scripts/router.py check-env` names both.

**Is `xhigh` available on every model?**
No. On a model without it, Claude Code quietly runs the highest level it supports (for example `high`).

**Is climbing from the cheapest model up the cheapest way?**
Not always: a failed attempt is paid for too. The benchmark compares "always one rung", "start at the bottom and climb", the router, and the cheapest rung that would have worked in hindsight.

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

## Contributing

`python3 -I tests/test_router.py` must print `0 failed`, and `claude plugin validate . --strict` must pass. See [AGENTS.md](AGENTS.md) and [CHANGELOG.md](CHANGELOG.md).

## Cite

See [CITATION.cff](CITATION.cff), or use GitHub's "Cite this repository" button.

## License

MIT. See [LICENSE](LICENSE).
