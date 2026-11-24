# Publishing steps (for the owner)

Claude prepared everything locally. These steps need your GitHub login, so you do them; each takes a minute.

## 1. Create the empty repository
On github.com: **New repository**
- Owner: your account. Name: `model-effort-router`
- Description (paste):
  `Claude Code plugin that picks the model (Sonnet or Opus) and effort for each task and subagent automatically, from the task, its labels and your repository, and checks the rules on a public SWE-bench Verified benchmark. Standard-library Python, MIT.`
- **Public**. Do NOT add a README, licence or .gitignore (the repository already has them).

## 2. Tell Claude "the repo is created"
Claude then runs `git remote add origin https://github.com/islamkmansoori/model-effort-router.git` and `git push -u origin main --tags`.

## 3. Settings on the repository page (gear icon next to "About")
- Website: `https://islamkmansoori.github.io/model-effort-router/`
- Topics (paste one by one): `claude-code` `claude-code-plugin` `claude` `anthropic` `subagents` `llm-routing` `model-routing` `model-selection` `reasoning-effort` `ai-agents` `agentic-coding` `llm-cost` `cost-optimization` `swe-bench` `benchmark` `developer-tools` `python`
- Tick **Releases** and **Packages** off if you like; leave **Releases** on.

## 4. GitHub Pages (the crawlable website)
Settings, Pages: Source **Deploy from a branch**, branch `main`, folder `/docs`. Save. The site appears at the Website address above in a minute or two.

## 5. Social preview
Settings, General, Social preview: upload `assets/social-preview.png` (shown when the link is shared).

## 6. Release
Releases, **Draft a new release**: choose tag `v0.3.0` (already pushed), title `v0.3.0`, paste the 0.3.0 section of `CHANGELOG.md` and the results table from `bench/results/REPORT.md`. Publish.

## 7. Get it listed (optional, under your name)
See `LISTINGS.md` for the texts. Each is a pull request or a form you submit yourself.
