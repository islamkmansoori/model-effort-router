# Changelog

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
