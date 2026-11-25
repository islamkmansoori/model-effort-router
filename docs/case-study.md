# Case study: what model choice saved on a real project

**The project.** A private, multi-agent software build over 18 days (7 to 24 November 2026): one coordinating Claude Code session at a time, many subagents, and separate worker sessions, on a codebase of several services. The project itself stays private; only the totals below are published, produced by `router.py savings` from Claude Code's own usage records.

**What this measures.** How much cheaper the project ran because its sessions and subagents used a mix of models (mostly Sonnet 5.5, some Opus 5.5, Haiku 5.5 for background work, and Fable 5.2 for some planning), compared with the same tokens all on Opus 5.5.

**What it does not measure.** The router's own contribution. The plugin was never enabled in the Claude Code on this machine, so the hook never chose a model here: every model in the table was chosen by hand or by Claude Code's defaults. The 29% is the size of the prize the router competes for, not its result. The second machine (a Windows PC running worker sessions) chose its models with the project's own chooser, a separate script, not with this plugin; its figures are not yet collected.

## Result

Estimated from Claude Code's own usage records: 19,226 model replies in 43 sessions, 2026-11-07 to 2026-11-24. Prices: Anthropic API list prices per token (a subscription is billed differently; read these as API-equivalent dollars). Baseline: the same tokens on claude-opus-5-5.

| Who | Model | Replies | Tokens | Estimated cost | Same tokens on the baseline |
|---|---|---|---|---|---|
| main sessions | claude-fable-5-2 | 578 | 184,144,382 | $138.27 | $87.07 |
| main sessions | claude-haiku-5-5 | 878 | 183,968,824 | $4.43 | $142.72 |
| main sessions | claude-opus-5-5 | 1,510 | 741,348,475 | $250.46 | $250.46 |
| main sessions | claude-sonnet-5-5 | 3,826 | 1,410,242,048 | $269.48 | $538.97 |
| subagents | claude-fable-5-2 | 841 | 204,358,025 | $230.15 | $133.98 |
| subagents | claude-haiku-5-5 | 75 | 5,030,687 | $0.18 | $6.43 |
| subagents | claude-opus-5-5 | 3,209 | 694,085,458 | $303.02 | $303.02 |
| subagents | claude-sonnet-5-5 | 8,309 | 1,552,337,334 | $389.71 | $779.43 |

- **Subagents:** $923.07 against $1,222.86 on the baseline: saved $299.79 (25%).
- **Main sessions:** $662.64 against $1,019.21 on the baseline: saved $356.57 (35%).
- **All:** $1,585.71 against $2,242.06 on the baseline: saved $656.35 (29%).

Same tokens on the baseline assumes the baseline model would have used the same number of tokens; a stronger model may need fewer turns, a weaker one more. Subagents are where the router chooses.

## Reading it

- **Mixing models saved about 29%** (an estimated $656 in API-equivalent dollars) against running every reply on Opus 5.5.
- **Sonnet did most of the work.** 67% of subagent replies ran on Sonnet 5.5, at half Opus's price per token.
- **Choosing the expensive model costs too.** Fable 5.2 replies cost more than the same tokens on Opus 5.5 would have; a router has to justify the top rung, not only the bottom one.
- **This project ran on a subscription.** These are API-list-price equivalents for comparison, not an invoice.

## The router's first two days on this machine (24–25 November)

The same command, for 24 and 25 November only, excluding the session that built the router:

| Who | Replies | Estimated cost | Same tokens on the baseline |
|---|---|---|---|
| Subagents | 173 | $15.07 | $14.13 (the router's costs $0.94 more, 7%) |
| Main sessions | 637 | $91.82 | $117.27 (saved 22%) |
| All | 810 | $106.89 | $131.39 (saved 19%) |

**These figures do not measure the router.** The hook log that the plugin writes (`router.py audit`) does not exist on this machine, so no subagent was routed by it. The subagent models above were whatever the sessions used: mostly Fable 5.2 and Haiku 5.5, neither of which is on the router's ladder. The main-session saving came from choices made by hand. Two days and a few hundred replies are also too few to judge anything.

The router's own effect needs a machine where the plugin is enabled, and `router.py audit` run there first, to show that the hook routed subagents. Only then does `savings` measure something the router did.

## Measure your own

```
python3 scripts/router.py savings --projects-dir ~/.claude/projects/<your project folder> --since 2026-11-01
python3 scripts/router.py savings ... --baseline claude-sonnet-5-5   # compare with another model
python3 scripts/router.py savings ... --json                         # for your own charts
```

Then share the totals through the "Share results" issue form: every project that publishes its numbers makes the rules better.
