# Benchmark results

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

## Each task

| Task | Difficulty | Router chose (rules / assessor) | sonnet medium | sonnet high | opus high | opus xhigh |
|---|---|---|---|---|---|---|
| pylint-dev__pylint-4604 | 15 min - 1 hour | sonnet medium / sonnet medium | not solved $0.06 | not solved $0.07 | not solved $0.19 | not solved $0.22 |
| pylint-dev__pylint-4970 | <15 min fix | sonnet medium / sonnet medium | not solved $0.06 | not solved $0.09 | solved $0.17 | solved $0.30 |
| pylint-dev__pylint-6386 | 15 min - 1 hour | sonnet medium / sonnet medium | not solved $0.15 | solved $0.12 | solved $0.35 | solved $0.50 |
| pylint-dev__pylint-8898 | 1-4 hours | sonnet medium / sonnet medium | solved $0.13 | solved $0.13 | solved $0.29 | solved $0.38 |
| pytest-dev__pytest-10051 | 15 min - 1 hour | sonnet medium / sonnet medium | solved $0.08 | solved $0.08 | solved $0.20 | solved $0.22 |
| pytest-dev__pytest-10081 | <15 min fix | sonnet medium / sonnet medium | solved $0.06 | solved $0.07 | solved $0.12 | solved $0.18 |
| pytest-dev__pytest-10356 | 1-4 hours | sonnet medium / sonnet medium | not solved $0.09 | not solved $0.09 | solved $0.27 | solved $0.32 |
| pytest-dev__pytest-5631 | 15 min - 1 hour | sonnet medium / sonnet medium | solved $0.05 | solved $0.05 | solved $0.10 | solved $0.13 |
| pytest-dev__pytest-5787 | 1-4 hours | sonnet medium / sonnet medium | not solved $0.17 | not solved $0.18 | solved $0.42 | solved $0.43 |
| pytest-dev__pytest-5809 | <15 min fix | sonnet medium / sonnet medium | solved $0.05 | solved $0.05 | solved $0.12 | solved $0.12 |
| pytest-dev__pytest-5840 | 15 min - 1 hour | sonnet medium / sonnet medium | not solved $0.08 | not solved $0.11 | solved $0.43 | solved $0.51 |
| pytest-dev__pytest-6197 | 1-4 hours | sonnet medium / sonnet medium | not solved $0.06 | not solved $0.06 | solved $0.35 | solved $0.41 |
| pytest-dev__pytest-7432 | <15 min fix | sonnet medium / sonnet medium | solved $0.07 | solved $0.08 | solved $0.13 | solved $0.19 |
| pytest-dev__pytest-7571 | 15 min - 1 hour | sonnet medium / sonnet medium | solved $0.07 | solved $0.09 | solved $0.18 | solved $0.20 |
| pytest-dev__pytest-7982 | <15 min fix | sonnet medium / sonnet medium | solved $0.05 | solved $0.05 | solved $0.12 | solved $0.10 |

## Tuned on one half, tested on the other

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
