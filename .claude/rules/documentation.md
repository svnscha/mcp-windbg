---
paths:
  - "docs/**"
---

# Documentation (user guide) authoring

Authoring rules for the user guide under `docs/`, built with MkDocs Material and deployed to
GitHub Pages. Keep edits consistent with what is already there; the current length,
phrasing, and tone are the target. The repo-wide Markdown typography rules (plain hyphens,
no emojis) also apply here, see `markdown.md`.

The guide is a usage guide. Keep it short and scenario-first.

- Lead with the thing the reader copies, the natural-language request or the config block,
  then explain only what is specific to it.
- Link to the reference, do not re-explain. Tool parameters, CLI flags, and per-field detail
  live in `docs/reference/`. Scenario pages point there instead of repeating it.
- Do not over-explain the obvious. Trust the reader; skip generic VS Code, Windows, or MCP
  hand-holding.
- Plain, simple language. Short sentences. Sentence-case headings. American English.
- Parallel titles for the use-case pages: `Analyze a crash dump`, `Debug a remote target`,
  `Triage multiple dumps`.
- Admonitions sparingly (`!!! note|tip|warning`) and only for a genuinely non-obvious
  caveat, not as decoration.
- Tables for option, parameter, and tool lists; fenced code blocks with a language for every
  snippet (`text` for chat prompts, `json` for config, `bash`/`powershell` for commands).
- Prefer keeping the current word count. When in doubt, cut rather than add.

Capability facts come from the code, keep the reference in sync with them:

- Tool names and parameters: `src/mcp_windbg/server.py` (the Pydantic param models and the
  `list_tools` definitions).
- Command-line options: `src/mcp_windbg/__init__.py` (the `argparse` setup).

## Before committing

```powershell
pwsh scripts/Format-Docs.ps1            # typography (dashes, emoji)
python -m mkdocs build --strict         # builds clean, links resolve
```
