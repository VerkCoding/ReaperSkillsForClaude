#!/usr/bin/env python3
"""Check package files without external dependencies (patterned after humanizer)."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_package_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise SystemExit(f"Cannot read {path.relative_to(ROOT)}: {error}")


SKILL_PATH = ROOT / "SKILL.md"
SKILL = read_package_file(SKILL_PATH)
README = read_package_file(ROOT / "README.md")

try:
    PLUGIN = json.loads(read_package_file(ROOT / ".claude-plugin" / "plugin.json"))
except json.JSONDecodeError as error:
    raise SystemExit(f"Fix the JSON in .claude-plugin/plugin.json: {error}")

try:
    MARKETPLACE = json.loads(read_package_file(ROOT / ".claude-plugin" / "marketplace.json"))
except json.JSONDecodeError as error:
    raise SystemExit(f"Fix the JSON in .claude-plugin/marketplace.json: {error}")


def require_match(match: re.Match[str] | None, message: str) -> re.Match[str]:
    if match is None:
        raise SystemExit(message)
    return match


yaml_metadata = require_match(
    re.match(r"\A---\n(.*?)\n---\n", SKILL, re.DOTALL),
    "SKILL.md must begin with YAML metadata",
).group(1)

for unsupported_field in ("version:", "compatibility:", "allowed-tools:"):
    if re.search(rf"(?m)^{re.escape(unsupported_field)}", yaml_metadata):
        raise SystemExit(f"Remove unsupported YAML field: {unsupported_field[:-1]}")

skill_version = require_match(
    re.search(r'(?m)^\s+version:\s*["\']?([0-9]+\.[0-9]+\.[0-9]+)["\']?\s*$', yaml_metadata),
    "Add metadata.version to SKILL.md as a three-part version",
).group(1)

plugin_version = str(PLUGIN.get("version", ""))
if skill_version != plugin_version:
    raise SystemExit(f"Version mismatch: SKILL.md ({skill_version}) != plugin.json ({plugin_version})")

if not (ROOT / "SKILL.md").exists():
    raise SystemExit("Keep regular SKILL.md at the repo root")

if "./" not in PLUGIN.get("skills", []):
    raise SystemExit("Point the Claude plugin skill loader at the repo root (include './' in skills)")

# Validate marketplace plugin entries
plugins = MARKETPLACE.get("plugins", [])
if not plugins:
    raise SystemExit("Marketplace must declare at least one plugin")

plugin_names = [p.get("name") for p in plugins]
if PLUGIN.get("name") not in plugin_names:
    raise SystemExit(f"Plugin name '{PLUGIN.get('name')}' must be in marketplace.json plugins list: {plugin_names}")

print(f"ReaperSkillsForClaude package v{skill_version} is valid (aligned with humanizer structure)")
