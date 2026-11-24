# Contributing

Thank you for helping. The most useful contributions, in order:

1. **Results.** Run the router on your own tasks (`record`, then `summary`) or rerun the benchmark, and share what you found in an issue with the "Share results" form. Evidence of what predicts a task's difficulty is what this project needs most.
2. **Benchmark tasks.** More SWE-bench Verified projects that run without Docker, or a Docker runner for the rest (`bench/run.py`, `REPOS`).
3. **Rules.** A new signal, label or path rule is a config entry in `config/default.json` plus a test, backed by results.
4. **Bugs.** Use the bug report form.

## Rules for changes
- Standard-library Python 3.9+ only; no new dependency.
- Guesses never raise cost: risk or critical inferred from words or from the assessor is named, not acted on.
- The hook must never break a session: on any error it prints nothing and exits 0.
- `python3 -I tests/test_router.py` prints `0 failed`; `claude plugin validate . --strict` passes.
- Bump `version` in `.claude-plugin/plugin.json` and `VERSION` in `scripts/router.py` together, with a `CHANGELOG.md` entry.

More detail for coding agents: [AGENTS.md](AGENTS.md).
