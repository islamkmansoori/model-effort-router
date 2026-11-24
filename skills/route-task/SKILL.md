---
name: route-task
description: Choose the Claude model and effort for a task before handing it to a subagent or a separate worker session, and record how it went afterwards. Use when delegating a task written in a file (a story, an issue, a work packet), when a worker fails and may need a stronger model, when a task is finished, or when the user asks which model or effort a task should run on.
argument-hint: "[task file]"
---

# Route a task to a model and effort

The script is `${CLAUDE_PLUGIN_ROOT}/scripts/router.py` (Python 3.9+, standard library only; on Windows use `python` instead of `python3`). Run it from the project's root folder. It reads the project's rules from `.claude/model-router.json`, or uses its built-in rules when that file does not exist.

## Before starting a worker

1. Check that the task file has two lines. If one is missing, add it from what the task says, and tell the user what you chose:
   - `**Critical:** yes` or `no`: yes only when the task changes the part of the system where a mistake is most expensive (the project's config names it).
   - `**Risk:** none`, or one or more of `money`, `security`, `data`.
2. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" check-env`. Exit 1 means a setting on this machine overrides the choice (for example `CLAUDE_CODE_EFFORT_LEVEL`). Tell the user and do not start the worker until it is unset.
3. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" score <task file> --write`. It writes a line under the task's title: `**Model and effort (model-effort-router):** \`sonnet\`, effort \`medium\`. Score 3: ...`.
4. Start the worker on exactly that model and effort: with the Agent tool, its `model` and `effort` parameters; for a separate session, `claude --model <model> --effort <effort>`. Do not choose by hand.

## When a worker fails its own tests twice

Say whose failure it was before anything else. Count a failure only after the test set-up itself works.
- `worker` (the change is wrong): `score <task file> --write --retry 1 --cause worker` (then `--retry 2`), and restart the worker one rung up.
- `environment` (missing build output, a port, a broken tool) or `task` (it needs a decision only a person can make, or its criteria contradict each other): fix that or ask the user. A bigger model does not help, so the model and effort stay the same.

## When a task is finished

Ask the worker what its session actually ran on (`/effort status`, the session header, or `/tasks` for a subagent), not what it was told. Then:

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" record <task file> --ran <model>/<effort> --first-try pass|fail --retries <n> --other "<environment n, task n or none>" --findings <review findings> --note "..."`

A warning means what ran differs from what was chosen: run `check-env` on that machine. `summary` reads the log back. Change the points in `.claude/model-router.json` only from the log, after 10 to 20 tasks.

## New project

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" init` writes `.claude/model-router.json`. Edit its patterns so the critical guess, risk words and signals match the project's own words.
