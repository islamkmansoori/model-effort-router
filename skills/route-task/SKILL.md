---
name: route-task
description: Choose the Claude model and effort for a task before handing it to a subagent or a separate worker session, decide what to do when a worker fails, and record how it went. Use when delegating a task written in a file (a story, an issue, a work packet), a GitHub issue or a plain prompt; when a worker or its tests fail and you must decide between retrying, a stronger model, fixing the environment or asking a person; when a task is finished; or when the user asks which model or effort a task should run on, whether the router is working, what it chose, or how much is being spent; or when the user wants the whole team or project to get the router without installing it, or wants its updates to be automatic.
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
   Add `--assess auto`: when the task does not state its size, a cheap model reads it and the code and estimates the size and files; when it does, nothing is spent.
4. Read the "before starting" line, if there is one (vague criteria, an open question, no criteria). Tell the user and get the task fixed first when you can: an unclear task fails on every model.
5. Start the worker on exactly that model and effort: `claude --model <model> --effort <effort>`. Do not choose by hand.

## When a worker fails its own tests twice

Find out whose failure it was before anything else. Save the failing output to a file and run:

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" score <task> --write --retry 1 --failure-log <output file>` (then `--retry 2` after the next failure)

It suggests the cause from the output and acts on it:
- `worker` (the change is wrong): the choice moves to the next model. Restart the worker there.
- `environment` (a missing module, a full disk, a port, a rate limit) or `task` (it needs a decision only a person can make, or its criteria contradict each other): the model and effort stay the same, because a bigger model fixes neither. Fix the environment, or ask the user.
- The same failure on two different models: it stops climbing. Check the environment and the task.
- `STOP` (the retry limit is reached): do not start another worker. Tell the user what failed and ask.

If you know the cause better than the output shows, say it: `--retry 1 --cause worker|environment|task` wins over the suggestion. `router.py cause <output file>` shows the suggestion and its evidence without changing anything.

## When a task is finished

Ask what the session actually ran on (`/effort status`, the session header, or `/tasks` for a subagent), not what it was told. Then:

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" record <task> --ran <model>/<effort> --first-try pass|fail --retries <n> --other "<environment n, task n or none>" --findings <review findings> --note "..."`

A warning means what ran differs from what was chosen: run `check-env` on that machine. `summary` reads the log back and suggests point changes; apply them to `.claude/model-router.json` only after 10 to 20 tasks.

## When the user asks whether the router is working, what it is doing or what it costs

- `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" status`: one screen. Whether the hook is on, which rules are in use, whether a setting on this machine overrides the choice, and how many subagents it has routed in this project. Run this first, and read its lines to the user as they are (it also says whether the project carries its own copy, how updates arrive, and which Python runs it). Exit 1 means the hook is off or something overrides it.
- `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" audit`: how many subagent starts the hook routed, kept as the caller set them, skipped or capped.
- `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" budget`: API-equivalent spend today, this week and this month against the limits in the config (exit 1 when one is passed).
- `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" savings --since <YYYY-MM-DD>`: what the mix of models saved against running everything on the baseline model.

## New project

Nothing has to be set up: the built-in rules apply as soon as the plugin is installed. The hook's log (`.claude/model-router-runs.jsonl`) is kept out of git on this machine by a line in `.git/info/exclude`.

Only when the user wants their own rules: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" init` writes `.claude/model-router.json`. Edit it to the project's words: the critical and risk `paths` (globs such as `src/billing/*`), the signals, the labels, and the ladder.

## When the user wants everyone on the project to have it

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/router.py" share` (run it in the folder people start Claude Code in). It puts a copy of the router into `.claude/model-effort-router/` and two hooks into `.claude/settings.json`, and prints the `git add` line. Tell the user to commit both: from then on everyone who starts Claude Code in the project is routed, with no plugin to install. Do not commit for them unless they ask. In such a project this plugin's own hook stays silent, so nothing is routed twice. `share` again from a newer version updates the copy; `share --remove` takes it out.

## When the user wants updates to arrive by themselves

Claude Code keeps automatic updates off for plugins that are not Anthropic's, and this plugin never changes that by itself. When the user asks for it: find the settings file that declares the marketplace (`extraKnownMarketplaces` with a `model-effort-router` entry; usually `~/.claude/settings.json`, sometimes the project's `.claude/settings.json`) and add `"autoUpdate": true` next to its `source`, changing nothing else in the file. Or tell them the same switch is in `/plugin`, Marketplaces. `status` then shows "Updates: automatic". By hand: `claude plugin update model-effort-router@model-effort-router`.
