# ReaperSkillsForClaude

A Claude plugin: skills plus an MCP server for REAPER. Manifests in `.claude-plugin/`, skills in `skills/`, server in `src/reaper_mcp/`.

- Adding knowledge (topic documents, plugin workflows, new skills): follow [CONTRIBUTING.md](../CONTRIBUTING.md). Default to a topic document in `skills/reaper-audio-engineer/references/topics/`, not a new skill.
- Everything in the repository is in English. Documents written in another language are translated when imported, and their numbers compared with `scripts/compare-numbers.py`.
- Before committing: `python scripts/validate-package.py` and `claude plugin validate --strict .`
- No top-level `bin/` directory: claude.ai rejects plugins that ship one.
