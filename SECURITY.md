# Security

The router and its hook run locally and read only the task text and your repository. The only network calls are `--issue` (GitHub's API) and `--assess` (one Claude call through your own Claude Code), both only when you ask for them. The hook changes nothing but the `model` and `effort` of Agent tool calls and makes no permission decision unless you configure one.

To report a vulnerability, use GitHub's **private vulnerability reporting** (Security tab, "Report a vulnerability"). Please do not open a public issue for it.
