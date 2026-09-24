# Adding knowledge to the skills

The plugin grows by adding documents, not skills. Audio knowledge goes into topic documents under one skill, `reaper-audio-engineer`, and that skill's `SKILL.md` tells Claude which document to open for which job.

## 1. Where a new document goes

| You have | Put it in | Example |
| --- | --- | --- |
| Target numbers and methods for one audio subject (EQ, delay, saturation, de-essing…) | `skills/reaper-audio-engineer/references/topics/<subject>.md` | `compression.md` |
| A workflow for one third-party plugin | `skills/reaper-audio-engineer/references/topics/plugin-<vendor>-<product>.md` | `plugin-sonible-smartchain.md` |
| Parameter indices, value mappings or traps for driving a plugin from code | A section in `skills/reaper-mcp/references/plugin-control.md` | FabFilter Pro-C 3 |
| A new step in measuring and verifying | The process documents: `audio-recording.md`, `audio-mixing.md`, `audio-mastering.md` | G-stages in `audio-mixing.md` |
| A different job with its own triggers, not audio decisions in REAPER | A new skill (section 6) | — |

Default to a topic document. A topic costs nothing until Claude opens it, while every skill's description sits in context all the time and competes with the others to trigger. Twenty topics under one skill trigger more reliably than twenty skills with overlapping descriptions.

## 2. Importing an author's document

Keep the author's content. Limit changes to the steps below, so the document can be imported again when the author revises it.

1. **Translate** it into English and save it in `topics/` under an ASCII kebab-case name, as UTF-8 without BOM and with LF line endings. Translate the meaning faithfully: keep the structure, every number and unit, every source link, and REAPER's interface labels as REAPER shows them. Correct an internal reference only when it clearly points to the wrong section, and report the correction. The original stays outside the repository as the source.
2. **Compare the numbers** of the original and the translation with `scripts/compare-numbers.py` (section 5). Every difference it prints must be explained, such as a number written as a word.
3. **Add the role block** under the title and date line (template in section 4).
4. **Link other documents.** Turn references to other documents into relative links, for example `[gain-staging.md](gain-staging.md), section 5`. References to sections of the same document stay as text.
5. **Reconcile numbers** against the owners in section 3, and change the document that does not own the number. Where a document repeats a range as shorthand, point to the owner's table instead of restating it. List every changed number in the commit message.
6. **Append the mapping section** "Applying in this plugin" (template in section 4). It maps each manual step to a tool or the bridge and names the steps only the user can do. Check tool names and parameters against `src/reaper_mcp/*_tools.py`, not memory: every `analyze_*` tool renders and measures the whole project, and `set_fx_parameter` takes normalised values.
7. **Register it** with a row in the Topic library table of `skills/reaper-audio-engineer/SKILL.md`: Document, Covers, Read when. A plugin document's "Read when" names the FX as REAPER displays it, such as `VST3: smartChain (sonible)`.
8. **Link it from the process step** that needs it: a G-stage in `audio-mixing.md`, or a section of `audio-recording.md` or `audio-mastering.md`.
9. **Extend the description** only when the document brings a new trigger word, such as a plugin name. The skill's `description` must stay under 1024 characters.
10. **Bump the version and run the checks** (section 5).

## 3. Who owns which numbers

When two documents give different values, the owner wins. Fix the other document and report the change.

| Numbers | Owner |
| --- | --- |
| Input levels per source and stage, meters, headroom, loudness and true peak targets | `topics/gain-staging.md` |
| Compressor type, ratio, attack, release, knee, gain reduction, sidechain, parallel | `topics/compression.md` |
| Reverb type, decay, pre-delay, the reverb bus set, reverb ducking | `topics/reverb.md` |
| Measurement method, evidence tiers, verification | The process documents |
| A plugin's own workflow | Its `plugin-*.md`, taking targets from the owners above |

A new subject adds a row. A number with no owner belongs to the document that states it most specifically.

## 4. Templates

Role block, directly under the title and date line:

```markdown
> **In this plugin:** this document owns <which numbers or workflow>. <Figures taken from other documents follow those documents.>
> Written for hands-on work in the REAPER interface. The last section, "Applying in this plugin", maps each step to the MCP tools and the Lua bridge.
```

A plugin document also says which FX makes it relevant and which steps happen inside the plugin's own interface.

Mapping section, last in the document or just before "Sources":

```markdown
## Applying in this plugin

<One sentence: what Claude does, what the user does.>

| Step in this document | How to do it in the plugin |
| --- | --- |
| <manual step> | <an MCP tool; or the Lua bridge with the API name; or "the user clicks it in the interface"> |
```

Everything in the repository is in English, including topic documents imported from another language. `validate-package.py` rejects Vietnamese text in skill documents.

## 5. Checks before committing

```bash
python scripts/validate-package.py
```

```bash
claude plugin validate --strict .
```

After translating, compare the numbers of the original and the translation:

```bash
python scripts/compare-numbers.py "<original.md>" skills/reaper-audio-engineer/references/topics/<name>.md
```

`validate-package.py` checks that every relative link in the skills resolves inside the plugin, that skill documents contain no Vietnamese text, and that every topic document has an ASCII kebab-case name, a row in its `SKILL.md` and the mapping section. It also checks skill names and descriptions, duplicate marketplace entries, that there is no top-level `bin/` directory (claude.ai rejects plugins that ship one), and that the version matches in `.claude-plugin/plugin.json`, `pyproject.toml`, `src/reaper_mcp/__init__.py` and any skill `metadata.version`.

Bump the version in those files: minor for new documents, patch for corrections. Installed copies update only when the version changes.

## 6. When a new skill is warranted

Only for a job with its own triggers that no existing description covers. Then:

1. Create `skills/<name>/SKILL.md`. The name matches the directory, uses lowercase letters, digits and hyphens, and must not contain `claude` or `anthropic`. The description is at most 1024 characters, with no XML tags.
2. Add the path to `skills` in `.claude-plugin/plugin.json`. The marketplace entry's source is the repository root, so that list is the complete set: an unlisted skill does not load.
3. Add the skill to the routing table in `skills/reaper-core-setup/SKILL.md`.
4. Run the same checks and bump the version.
