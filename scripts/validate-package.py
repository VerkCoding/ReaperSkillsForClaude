#!/usr/bin/env python3
"""Check package files without external dependencies.

Run before pushing: `claude plugin validate --strict .` checks the manifest schema,
this script checks the rules it does not cover, including the claude.ai sync rules.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_package_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError as error:
        raise SystemExit(f"Cannot read {path.relative_to(ROOT)}: {error}")


def read_json(path: Path) -> dict:
    try:
        return json.loads(read_package_file(path))
    except json.JSONDecodeError as error:
        raise SystemExit(f"Fix the JSON in {path.relative_to(ROOT).as_posix()}: {error}")


def require_match(match: re.Match[str] | None, message: str) -> re.Match[str]:
    if match is None:
        raise SystemExit(message)
    return match


PLUGIN = read_json(ROOT / ".claude-plugin" / "plugin.json")
MARKETPLACE = read_json(ROOT / ".claude-plugin" / "marketplace.json")
plugin_version = str(PLUGIN.get("version", ""))

# claude.ai rejects a plugin with a top-level bin/ directory, on marketplace sync and on upload.
if (ROOT / "bin").exists():
    raise SystemExit("Remove the top-level bin/ directory: claude.ai rejects plugins that ship one. Keep executables in scripts/.")

# Validate each skill the plugin loads
skill_paths = PLUGIN.get("skills", [])
if not skill_paths:
    raise SystemExit("List the plugin's skills in plugin.json")

for skill_path in skill_paths:
    skill_dir = (ROOT / skill_path).resolve()
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        raise SystemExit(f"plugin.json lists {skill_path}, but {skill_path}/SKILL.md does not exist")

    yaml_metadata = require_match(
        re.match(r"\A---\n(.*?)\n---\n", read_package_file(skill_file), re.DOTALL),
        f"{skill_path}/SKILL.md must begin with YAML metadata",
    ).group(1)

    skill_name = require_match(
        re.search(r"(?m)^name:\s*(\S+)\s*$", yaml_metadata),
        f"Add a name to {skill_path}/SKILL.md",
    ).group(1)
    if skill_name != skill_dir.name:
        raise SystemExit(f"Skill name '{skill_name}' must match its directory '{skill_dir.name}'")

    # Agent Skills naming rules, enforced by claude.ai and the Skills API
    if len(skill_name) > 64 or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", skill_name):
        raise SystemExit(f"Skill name '{skill_name}' must be lowercase letters, numbers and hyphens, at most 64 characters")
    for reserved_word in ("anthropic", "claude"):
        if reserved_word in skill_name:
            raise SystemExit(f"Skill name '{skill_name}' must not contain the reserved word '{reserved_word}'")

    description = require_match(
        re.search(r"(?ms)^description:[ \t]*(?:[|>][-+]?[ \t]*\n)?(.*?)(?=^\S|\Z)", yaml_metadata),
        f"Add a description to {skill_path}/SKILL.md",
    ).group(1)
    description = " ".join(description.split())
    if not description or len(description) > 1024:
        raise SystemExit(f"{skill_path}/SKILL.md description must be 1-1024 characters (found {len(description)})")
    if re.search(r"<[^>]+>", description):
        raise SystemExit(f"{skill_path}/SKILL.md description must not contain XML tags")

    for unsupported_field in ("version:", "compatibility:", "allowed-tools:"):
        if re.search(rf"(?m)^{re.escape(unsupported_field)}", yaml_metadata):
            raise SystemExit(f"Remove unsupported YAML field from {skill_path}/SKILL.md: {unsupported_field[:-1]}")

    skill_version = re.search(r'(?m)^\s+version:\s*["\']?([0-9]+\.[0-9]+\.[0-9]+)["\']?\s*$', yaml_metadata)
    if skill_version and skill_version.group(1) != plugin_version:
        raise SystemExit(f"Version mismatch: {skill_path}/SKILL.md ({skill_version.group(1)}) != plugin.json ({plugin_version})")

    # Topic documents (CONTRIBUTING.md): kebab-case names, listed in SKILL.md, ending with the mapping section
    skill_text = read_package_file(skill_file)
    for topic in sorted((skill_dir / "references" / "topics").glob("*.md")):
        where = topic.relative_to(ROOT).as_posix()
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*\.md", topic.name):
            raise SystemExit(f"Rename {where}: topic documents use ASCII kebab-case names")
        if f"references/topics/{topic.name}" not in skill_text:
            raise SystemExit(f"Add {where} to the Topic library in {skill_path}/SKILL.md")
        if not re.search(r"(?m)^## Applying in this plugin\s*$", read_package_file(topic)):
            raise SystemExit(f"Add the mapping section to {where} (template in CONTRIBUTING.md, section 4)")

# Skill documents are in English (CONTRIBUTING.md, section 2): letters used only by Vietnamese mark an untranslated import.
# Relative links in skill documents must resolve inside the plugin. Code blocks are skipped.
VIETNAMESE = re.compile("[\u1ea0-\u1ef9\u0102\u0103\u0110\u0111\u01a0\u01a1\u01af\u01b0]")
for doc in sorted((ROOT / "skills").rglob("*.md")):
    where = doc.relative_to(ROOT).as_posix()
    text = read_package_file(doc)
    untranslated = VIETNAMESE.search(text)
    if untranslated:
        line = text.count("\n", 0, untranslated.start()) + 1
        raise SystemExit(f"Translate {where} into English: Vietnamese text at line {line} (CONTRIBUTING.md, section 2)")
    prose = re.sub(r"(?ms)^```.*?^```", "", text)
    for target in re.findall(r"\]\(([^)\s]+)\)", prose):
        if target.startswith("#") or re.match(r"[a-z][a-z0-9+.-]*:", target):
            continue
        resolved = (doc.parent / target.split("#", 1)[0]).resolve()
        if not resolved.exists():
            raise SystemExit(f"Broken link in {where}: {target}")
        if ROOT != resolved and ROOT not in resolved.parents:
            raise SystemExit(f"Link leaves the plugin in {where}: {target}")

# The version is duplicated for Python packaging conventions
for path, pattern in (
    (ROOT / "pyproject.toml", r'(?m)^version\s*=\s*"([^"]+)"'),
    (ROOT / "src" / "reaper_mcp" / "__init__.py", r'(?m)^__version__\s*=\s*"([^"]+)"'),
):
    found = require_match(re.search(pattern, read_package_file(path)), f"Cannot find the version in {path.relative_to(ROOT).as_posix()}").group(1)
    if found != plugin_version:
        raise SystemExit(f"Version mismatch: {path.relative_to(ROOT).as_posix()} ({found}) != plugin.json ({plugin_version})")

# The MCP server is declared in .mcp.json and referenced by plugin.json
mcp_servers = PLUGIN.get("mcpServers")
if isinstance(mcp_servers, str):
    mcp_file = ROOT / mcp_servers
    if not mcp_file.is_file():
        raise SystemExit(f"plugin.json points mcpServers at {mcp_servers}, which does not exist")
    if not read_json(mcp_file).get("mcpServers"):
        raise SystemExit(f"{mcp_servers} must declare mcpServers")

# Validate marketplace plugin entries
plugins = MARKETPLACE.get("plugins", [])
if not plugins:
    raise SystemExit("Marketplace must declare at least one plugin")

plugin_names = [p.get("name") for p in plugins]
if PLUGIN.get("name") not in plugin_names:
    raise SystemExit(f"Plugin name '{PLUGIN.get('name')}' must be in marketplace.json plugins list: {plugin_names}")

sources = [json.dumps(p.get("source"), sort_keys=True) for p in plugins]
if len(set(sources)) != len(sources) or len(set(plugin_names)) != len(plugin_names):
    raise SystemExit("Each marketplace entry needs its own name and source; duplicate entries install the same plugin twice")

print(f"ReaperSkillsForClaude package v{plugin_version} is valid ({len(skill_paths)} skills)")
