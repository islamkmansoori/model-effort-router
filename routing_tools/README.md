# Routing tools

The routing and token-optimisation modules of an earlier AI engineering project by the same author, which read a project's backlog and wrote the code for it. They are published here as a standalone tool: the original code, usable from the command line or as a library, with no dependency beyond the Python standard library (`rich` is optional and only used by the modules' own dashboards).

They are **not** what the router in this repository decides with. The router was built after them and tested on a benchmark; these are the ideas it grew from. See "How this relates to the router" below.

## Use

```
python3 routing_tools/cli.py tier STORY.md              # complexity tier and the model would pick
python3 routing_tools/cli.py tier STORY.md --llm        # ask a model first, rules as fallback (see below)
python3 routing_tools/cli.py route STORY.md             # fast, balanced or premium, with the reason
python3 routing_tools/cli.py classify "run tests with pytest"   # a model request, or a local tool (which command)?
python3 routing_tools/cli.py failure "no such column: fee"      # root-cause category of a failure
python3 routing_tools/cli.py sort A.md B.md C.md        # simple stories first, coding-heavy last
python3 routing_tools/cli.py guard US-42 --output out.txt --exit-code 1 --tokens 1800
python3 routing_tools/cli.py feedback record US-42 RC-002 "Action Request" DONE --model sonnet --cost 0.02
python3 routing_tools/cli.py feedback summary
python3 routing_tools/cli.py tokens                     # totals from .routing_data/token_usage.jsonl
python3 routing_tools/cli.py retries                    # retry statistics from .routing_data/retry_history
```

A text argument may be a file path, a quoted string, or `-` for standard input. Add `--json` to any command. On Windows use `python`. State is kept under `.routing_data/` in the current folder.

`tier --llm` asks a model before the rules. Set `ROUTING_LLM_URL` to an OpenAI-compatible chat-completions endpoint (for example a LiteLLM proxy), `LITELLM_MASTER_KEY` to its key if it needs one, and `ROUTING_LLM_MODEL` to the model name (default `gemini-flash`). Without `ROUTING_LLM_URL` no request is made.

## Modules

| Module | What it does |
|---|---|
| `complexity_scorer.py` | Scores a story into four tiers (simple, mid, complex, coding_heavy) with weighted keyword rules, acceptance-criteria counts and length; maps each tier to a model; optional model-first classification; backlog sort |
| `complexity_classifier.py` | A faster heuristic: light, medium or heavy from prompt length, file count and a few keywords |
| `request_classifier.py` | Decides whether a request needs a model or a local tool (lint, tests, diff, git, docker), a ten-type request taxonomy with priority and confidence, and root-cause categories for failures |
| `model_router.py` | Chooses a model tier (fast, balanced, premium) from the request type and complexity words, or from a complexity result; sorts available models by fit |
| `routing_authority.py` | A lock that lets only authorised callers reach a model provider, with an audit log of approved and blocked calls |
| `classifier_feedback.py` | Records each classification with its outcome and reports precision and average cost per category |
| `llm_guard.py` | Per-story retry history: detects a repeated output (a loop) and an exhausted token budget |
| `token_logger.py` | Per-request token and cost log (JSONL) with daily, weekly and monthly totals, per-model statistics, a model leaderboard, budget checks and CSV export |
| `retry_analytics.py` | Retry rates, top-retrying stories, failure reasons and alerts from the retry history |
| `prompt_cache.py` | A disk-backed cache for repeated prompt contexts: content keys, expiry, least-recently-used eviction, hit rate |
| `cost_calculator.py` | An example cost and cache-savings calculator |
| `safe_io.py` | File writes that survive macOS quarantine restrictions |

As a library, add the repository root to `sys.path` and import, for example `from routing_tools.complexity_scorer import ComplexityScorer`.

## Where the code comes from, and what was changed

Copied from the earlier project's repository at commit `d305040` (9 October 2026), under Apache-2.0. Logic is unchanged. These are the changes, all made on 25 November 2026:

- **Names:** the folder, the module names in comments, and the word for the earlier project are gone from comments, docstrings, messages and identifiers (for example the environment variables are `ROUTING_LLM_URL` and `ROUTING_LLM_MODEL`, the data folder is `.routing_data`, and the caller prefix is `routing.`).
- `complexity_scorer.py`: the model call reads its endpoint, key and model name from the environment and is skipped when no endpoint is set. The original called a local proxy with a built-in default key; no key is shipped here.
- `routing_authority.py`: the caller prefix and the token string are renamed, so a caller must be a `routing.*` module.
- `model_router.py`: its two imports point at this package.
- `token_logger.py`, `prompt_cache.py`, `llm_guard.py`, `retry_analytics.py`, `classifier_feedback.py`: the default data folder is `.routing_data`.
- `request_classifier.py`: the model version string and two suggested test commands are renamed.

Written new for this folder: `cli.py` (the command line), `config.py` (a small stand-in for the earlier project's product-wide config loader; it reads `routing_tools.json` if present), `__init__.py` and `__main__.py`.

Left out: the executor that runs stories, the learned router (it needs RouteLLM and an embedding model), and everything unrelated to routing.

## How this relates to the router

`model-effort-router` was tested on 21 SWE-bench Verified tasks run on four model settings (84 runs). The result for the keyword tiers in `complexity_scorer.py`: starting the top tier on Opus cost $4.21 for the same 20 solved tasks, against $4.01 for starting everything on the cheap model and jumping to Opus on failure. So the router does not use these tiers to choose a model; `bench/keyword_tiers.py` keeps the same rules as a benchmark baseline, and a test checks the two stay identical. The scorer was written for user stories, and the benchmark tasks are bug reports, so read that result with that in mind.

What the router did take from this code, rebuilt to fit Claude Code: root causes for failures, the repeated-failure guard, the retry limit, budget alerts, outcome feedback, and the audit of routing decisions.

## Licence

Apache License 2.0: see [LICENSE](LICENSE) in this folder. The rest of this repository is MIT.
