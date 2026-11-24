---
name: route-task
description: Choose the Claude model and effort for a task before handing it to a subagent or a separate worker session, and record how it went afterwards. Use when delegating a task written in a file (a story, an issue, a work packet), a GitHub issue or a plain prompt; when a worker fails and may need a stronger model; when a task is finished; or when the user asks which model or effort a task should run on.
argument-hint: "[task file | owner/repo#123 | \"prompt\"]"
---

# Route a task to a model and effort

The script is `${CLAUDE_PLUGIN_ROOT}/scripts/router.py` (Python 3.9+, standard library only; on Windows use `python` instead of `python3`). Run it from the project's root folder. It reads the project's rules from `.claude/model-router.json`, or uses its built-in rules when that file does not exist.

**Subagents are routed automatically.** This plugin's hook sets `model` and `effort` on every Agent tool call that does not name them already (skipped types and the log are in the config's `hook` section). To help it, put `**Critical:** yes|no` and `**Risk:** none|money|security|data` in the subagent's prompt when you know them. Name a model or effort yourself only when the user asked for one: an explicit choice turns the routing off for that call.

## Before starting a separate worker session

1. Make sure the task states two facts, and add them if missing (tell the user what you chose):
   - `**Critical:** yes` or `no`: yes only when it changes the part of the system where a mistake costs most.
   - `**Risk:** none`, or one or more of `money`, `security`, `data`.
2. `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" check-env`: exit 1 means a setting on this machine overrides the choice (for example `CLAUDE_CODE_EFFORT_LEVEL`). Tell the user; do not start the worker until it is unset.
3. Score it, with the repository's facts:
   - a file: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" score <task file> --repo . --write` (writes the choice under its title)
   - a GitHub issue: `... score --issue owner/repo#123 --repo .`
   - a plain prompt: `... score --text "<prompt>" --repo .`
   Add `--assess` when the task says little about its size (a short issue, a one-line prompt): a cheap model reads it and the code and estimates the size and files. It costs a model call, so do not use it for tasks that state their size.
4. Start the worker on exactly that model and effort: `claude --model <model> --effort <effort>`. Do not choose by hand.

## When a worker fails its own tests twice

Say whose failure it was first. Count a failure only after the test set-up itself works.
- `worker` (the change is wrong): `score <task> --write --retry 1 --cause worker` (then `--retry 2`), and restart one rung up.
- `environment` (missing build output, a port, a broken tool) or `task` (it needs a decision only a person can make, or its criteria contradict each other): fix that or ask the user. A bigger model fixes neither, so the model and effort stay the same.

## When a task is finished

Ask what the session actually ran on (`/effort status`, the session header, or `/tasks` for a subagent), not what it was told. Then:

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" record <task> --ran <model>/<effort> --first-try pass|fail --retries <n> --other "<environment n, task n or none>" --findings <review findings> --note "..."`

A warning means what ran differs from what was chosen: run `check-env` on that machine. `summary` reads the log back and suggests point changes; apply them to `.claude/model-router.json` only after 10 to 20 tasks.

## New project

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" init` writes `.claude/model-router.json`. Edit it to the project's words: the critical and risk `paths` (globs such as `src/billing/*`), the signals, the labels, and the ladder.
