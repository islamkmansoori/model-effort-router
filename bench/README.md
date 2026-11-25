# Benchmark

Does the router choose a model and effort that solves real tasks for less? This folder answers that with public, repeatable runs.

**Tasks.** [SWE-bench Verified](https://www.swebench.com/): real GitHub issues, each with a fix and hidden tests checked by people. We use the pure-Python projects that run without Docker (pytest, pylint, flask): `sets.json` lists the `pilot` (5 pytest tasks across the difficulty levels) and the `full` set (30 tasks).

**Runs.** Every task runs once on every rung of the default ladder (Sonnet medium, Sonnet high, Opus high, Opus xhigh) with Claude Code in headless mode, in a fresh copy of the repository with its own virtual environment. Claude may only read, edit and run that copy's tests, and each run has a dollar cap. User customizations are switched off (`--bare` with an API key, or `--safe-mode` with a Claude login). Solved means every hidden test passes, graded like SWE-bench: the test files run and each test's status is read.

**Report.** Because every rung runs on every task, any way of choosing can be scored afterwards from the same runs:
- always one rung;
- start at the bottom and climb on failure (paying for every attempt);
- the router's choice, with rules only and with the assessor (its cost included), alone and jumping on failure;
- a keyword-tier scorer from an earlier project (`keyword_tiers.py`), its top tier starting on Opus: a baseline to beat, and a template for trying your own word list against the same runs;
- the cheapest rung that worked, known only in hindsight: the floor no method can beat.

```
python3 bench/run.py fetch
python3 bench/run.py validate --set pilot              # free: the known fix passes on this machine
python3 bench/run.py route --set pilot                 # free
python3 bench/run.py route --set pilot --assess --auth login   # one cheap model call per task
python3 bench/run.py route --set full --assess --reuse-assessed  # free: recompute from the stored estimates
python3 bench/run.py tune                                      # free: tune on half the tasks, test on the other
python3 bench/run.py run --set pilot --total-budget 30 --auth login --jobs 2
python3 bench/run.py report                            # results/REPORT.md, results/report.json
```

Needs git, [uv](https://docs.astral.sh/uv/) and Claude Code. `--auth api` uses `ANTHROPIC_API_KEY` and is billed; `--auth login` uses your Claude login and counts against your plan. Raw results are in `results/runs.jsonl` (one line per run: cost, turns, time, tests passed). A run that was interrupted resumes where it stopped.

**Honest limits.** One run per task and rung: model output varies from run to run, so a small set is a pilot, not proof. Costs are Claude Code's own `total_cost_usd` estimate. SWE-bench tasks are bug fixes in open-source libraries; your tasks may differ, which is why the router also keeps your own outcome log.
