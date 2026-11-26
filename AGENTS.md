# Working on model-effort-router

For AI coding agents and people changing this repository.

- **What it is.** A Claude Code plugin that chooses the model and effort for a task or a subagent. `scripts/router.py` does the scoring and the command line, `scripts/route_agent.py` is the PreToolUse hook for the Agent tool, `config/default.json` holds every rule, `skills/route-task/SKILL.md` tells Claude how to use it, and `bench/` is the SWE-bench benchmark.
- **Standard library only, Python 3.9+.** Do not add a dependency. Do not use syntax newer than 3.9 at run time (`X | Y` types only inside annotations, which `from __future__ import annotations` keeps as strings).
- **Rules live in the config, not the code.** A new signal, risk kind, label or path rule is a config entry plus a test, not a new `if`.
- **Guesses never raise cost.** Risk and critical inferred from words, or suggested by the assessor, are only *named* in the reasons. Only explicit lines (`**Critical:**`, `**Risk:**`), labels and configured paths act. Keep it that way: it is the main design promise, and the benchmark showed why (one word in a pasted package list once sent a task to the top rung).
- **A model is blamed last.** In `suggest_cause`, environment evidence wins over task evidence, which wins over worker evidence, and an unknown cause earns no step.
- **Advice does not route.** The readiness check reports; it never changes the model or effort.
- **New rules need evidence.** `bench/keyword_tiers.py` is a baseline that lost to "start cheap, jump on failure". Before adding a signal that raises the first choice, score it against `bench/results/runs.jsonl`.
- **The hook must never break a session.** Any error in `route_agent.py` means: print nothing, exit 0.
- **The hook commands live in one place.** `router.hook_commands()` builds the two shell commands; `hooks/hooks.json` (the plugin) and `share` (a project's own copy) both use them, and a test keeps `hooks/hooks.json` identical. Change the function, then regenerate the file.
- **A project's copy and the plugin must never both route.** `route_agent.py` stays silent when the session's start folder carries a shared copy. Keep that check when touching the hook.
- **Releases publish themselves.** Change `version` in `.claude-plugin/plugin.json` (and `VERSION` in `scripts/router.py`, `CITATION.cff`, `docs/index.html`), add the version's section to `CHANGELOG.md`, push to main. CI tags and releases it after the tests pass. Plugin users only receive a change when the version changes.
- **Plug and play is a promise.** After the one-line install nothing else may be required: no config file, no command to run, no file to add to `.gitignore`. A short project config (one section, one field) has to keep working; `load_config` lays it over the defaults, and a test runs the hook with each short file the README suggests.
- **Test before you finish.** `python3 -I tests/test_router.py` must print `0 failed`. CI runs it on Linux, macOS and Windows with Python 3.9, 3.12 and 3.13.
- **Validate the plugin.** `claude plugin validate . --strict` must pass.
- **The benchmark costs money or plan usage.** `bench/run.py validate` and `route` (without `--assess`) are free; `run` and `route --assess` call Claude. Never start a paid run without a `--total-budget`.
- **`routing_tools/` is original code of an earlier project by the same author, under Apache-2.0.** Keep it as it is: no refactoring, no reformatting. A change to a copied module needs a line at the top of that file saying what changed and when, and a line in `routing_tools/README.md`. New code for it goes in `cli.py` or `config.py`. `python3 -I tests/test_routing_tools.py` must print `0 failed`.
- **Versions.** Bump `version` in `.claude-plugin/plugin.json` and `VERSION` in `scripts/router.py` together, and add a `CHANGELOG.md` entry.
