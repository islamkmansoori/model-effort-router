# Savings people have reported

Added up from the reports people chose to post in [Discussions](https://github.com/islamkmansoori/model-effort-router/discussions) with `router.py report`. Each report is that person's own estimate, from Claude Code's usage records at API list prices; nobody has verified them. The router sends nothing by itself: a report exists only because someone published it.

**No reports yet.** To add yours, run `python3 scripts/router.py report` in a project where the router is on (or ask Claude to share what the router saved), read the post it prints, and publish it if you want to.

How this is put together: `scripts/collect_reports.py` on the main branch. The newest report per person and project counts, so posting again never counts twice.
