---
paths:
  - "**/*.md"
---

# Markdown typography

Applies to every Markdown file in the repo (README, CLAUDE.md, the docs guide, these
rules).

- Use a plain hyphen `-`. Never use an em dash (U+2014, the long `-`) or an en dash
  (U+2013). Write ranges as "0 to 9" or with a hyphen, and break a clause with a comma,
  parentheses, or a new sentence instead of a dash.
- No emojis anywhere. The fix is wording, not deletion (for example write "Planned", not a
  construction-sign emoji), so they are reported, not stripped.

Run the formatter, which scans every `*.md` (skipping vendored/generated trees):

```powershell
pwsh scripts/Format-Docs.ps1            # fix dashes, report emoji
pwsh scripts/Format-Docs.ps1 -Check     # verify only (non-zero exit if not clean)
```
