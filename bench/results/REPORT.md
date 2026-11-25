# Benchmark results

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
| pytest-dev__pytest-6202 | <15 min fix | sonnet medium / sonnet medium | solved $0.05 | solved $0.06 | solved $0.18 | solved $0.21 |
| pytest-dev__pytest-7205 | <15 min fix | sonnet medium / sonnet medium | solved $0.06 | solved $0.07 | solved $0.17 | solved $0.31 |
| pytest-dev__pytest-7236 | 15 min - 1 hour | sonnet medium / sonnet medium | solved $0.07 | solved $0.10 | solved $0.20 | solved $0.22 |
| pytest-dev__pytest-7324 | 15 min - 1 hour | sonnet medium / sonnet medium | solved $0.08 | solved $0.09 | solved $0.19 | solved $0.18 |
| pytest-dev__pytest-7432 | <15 min fix | sonnet medium / sonnet medium | solved $0.07 | solved $0.08 | solved $0.13 | solved $0.19 |
| pytest-dev__pytest-7490 | 15 min - 1 hour | sonnet medium / sonnet medium | solved $0.09 | solved $0.09 | solved $0.24 | solved $0.25 |
| pytest-dev__pytest-7571 | 15 min - 1 hour | sonnet medium / sonnet medium | solved $0.07 | solved $0.09 | solved $0.18 | solved $0.20 |
| pytest-dev__pytest-7982 | <15 min fix | sonnet medium / sonnet medium | solved $0.05 | solved $0.05 | solved $0.12 | solved $0.10 |
| pytest-dev__pytest-8399 | 15 min - 1 hour | sonnet medium / sonnet medium | solved $0.05 | solved $0.05 | solved $0.21 | solved $0.11 |

## Tuned on one half, tested on the other

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
