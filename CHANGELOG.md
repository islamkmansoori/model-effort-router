# Changelog

## 0.6.0
- `routing_tools/`: the routing and token-optimisation modules of an earlier project by the same author (complexity scorer, request classifier, model router, routing authority, classifier feedback, loop guard, token logger, retry analytics, prompt cache), published as a standalone tool with a command line. Apache-2.0; the rest of the repository stays MIT. One behaviour change: the scorer's optional model call takes its endpoint and key from the environment only, and no default key is shipped.
- A test keeps `bench/keyword_tiers.py` identical to the scorer of that earlier project.

## 0.5.0
Mechanisms from an earlier agent-orchestration project by the same author, rebuilt without dependencies, and one fix the benchmark asked for.
- `cause` and `score --retry N --failure-log FILE`: the cause of a failure (worker, environment or task) is suggested from the failing output. Environment evidence wins over task evidence, which wins over worker evidence.
- The same failure on two different models earns no further step; after `max_worker_retries` (2) the router says to stop and ask a person.
- A later failure that is not the worker's keeps the steps earlier failures earned (it used to reset the choice).
- Readiness: every score reports vague or too-short acceptance criteria, an open question, or missing criteria. Advice only.
- `budget`: daily, weekly and monthly limits with alerts, from Claude Code's usage records; optional cap in the hook (`budget.throttle`).
- `audit`: what the hook did (routed, kept, skipped, capped, overridden).
- `--assess auto` (and `hook.assess: "auto"`): the assessor runs only when the task does not state its size.
- Optional mechanical rung (off by default): short tasks that only run a tool go to Haiku.
- **Changed default:** a critical part guessed from wording is now only named, like a guessed risk (`critical.guess_acts`). In the benchmark the word "cryptography" in a pasted package list had sent a task to Opus xhigh.
- Benchmark: a keyword-tier scorer added as a baseline (`bench/keyword_tiers.py`): its top tier on Opus costs $4.21 against $4.01 for starting cheap. `route --assess --reuse-assessed` recomputes routes from stored estimates without model calls.

## 0.4.0
- `router.py savings`: estimates what model choice saved from Claude Code's own usage records (each reply counted once, cache reads and writes priced, subagents apart from main sessions, totals only). Prices live in the config.
- Case study: on a real 18-day multi-agent project, mixing models saved about 29% against all-Opus.
- Community files (contributing, code of conduct, security, issue forms, PR template); crawler files for the Pages site.

## 0.3.0
- Benchmark results: 21 SWE-bench Verified tasks on four rungs (84 runs). Starting on Sonnet medium and jumping to Opus high on failure solved 20/21 for 13% less than always Opus high.
- Default changed from the evidence: a worker retry now jumps to the next model (`"retry_jumps_model": true`).
- `bench/run.py tune`: tune on one half of the tasks, report on the other.
- Optional assessor: one cheap model call (Haiku, low effort, read-only, under a cent per task in our runs) estimates the size and the files likely to change when the task text does not say. Its risk and critical estimates are only named.
- Public benchmark (`bench/`): SWE-bench Verified tasks run on every rung; the report compares fixed choices, climbing from the bottom, the router (rules only and with the assessor) and the cheapest rung that worked.
- Plugin icon; marketplace description; CI on Linux, macOS and Windows; `llms.txt`; `AGENTS.md`.

## 0.2.0
- Inputs: GitHub issues (`--issue owner/repo#N` or URL), plain prompts (`--text`, `-` for standard input).
- Repository facts (`--repo`): files the task names, files without tests, often-changed files.
- Labels and configured paths for size, critical and risk.
- `summary` suggests point changes from the outcome log.
- PreToolUse hook: routes every subagent the Agent tool starts, automatically. Checked live: the subagent ran on the model the hook chose.

## 0.1.0
- Score a task file; explicit `**Critical:**` and `**Risk:**` lines; retries only for the worker's own failures; `check-env`; outcome log.
