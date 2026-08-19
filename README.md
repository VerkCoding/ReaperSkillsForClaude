# REAPER for Claude - WIP

This project integrates Claude with the REAPER DAW. It provides tools for mixing, mastering, MIDI, FX, rendering, and DSP measurement.

The integration consists of three agent skills and an MCP server containing 58 REAPER tools. It supports Claude Code, Claude Desktop, and claude.ai.

```
You:    Master this to -14 LUFS, keep the transients.
Claude: [measures the actual loudness, sets the chain, renders, measures again]
```

| | Claude Code | Claude Desktop | claude.ai (web) |
| --- | --- | --- | --- |
| Skills: setup, channel, craft | yes | yes | yes |
| MCP server, 58 REAPER tools | yes | yes | **no** |
| Lua file bridge | yes | where shell access exists | **no** |

The claude.ai web application cannot access a local MCP server because it runs in the browser.

---

## Install

**Windows.** The components installed are cross-platform. See
[Porting](DOCS.md#porting) for other operating systems.

**1.** Clone the repository:

```bash
git clone https://github.com/VerkCoding/ReaperSkillsForClaude.git
```

**2.** Run **`RunThisToStart.bat`** and select **`[1] Install Everything`**.

The script backs up current settings, installs required dependencies, and configures the plugin. A Claude sign-in is required if it is not already installed on the system.

**3.** Start **REAPER**, then start **Claude**, and prompt:

> Check the current REAPER project info

If the project information is returned, the installation is complete.

> [!NOTE]
> **If REAPER shows a "ReaScript task control" dialog** stating
> *"activate_reapy_server.py is running in the background"*, choose **New
> instance**. Do not choose **Terminate instances**, as this stops the server
> and the bridge.

### Troubleshooting

```powershell
powershell -ExecutionPolicy Bypass -File install\health-check.ps1
```

The script outputs `[ok]`, `[warn]`, or `[FAIL]` for each step. Fixes are provided for failures. The **reaper-core-setup** skill also contains troubleshooting information.

For additional documentation, see **[DOCS.md](DOCS.md)**.

---

## Roadmap

The plugin currently operates on Windows. The following items remain incomplete:

**Before a wider release**

- [ ] Finish Reaper-MCP skills
- [ ] Finish Reaper-Audio-Engineer skills
- [ ] Links Everything with Reaper-Core-Setup skills

**Wanted**

- [ ] Make Tutorial Video
- [ ] ON GOD FIX EVERY BUG
- [ ] Linux && MacOS

**Known and deliberate limitations**

- Claude Code is installed using winget instead of the cache. Winget extracts the package instead of running an installer.
- The installer skips REAPER and Claude if they are already present on the system. It does not repair existing installations.

---

## Credits and Dependencies

This plugin uses code and concepts from three other REAPER projects.

**[xDarkzx/Reaper-MCP](https://github.com/xDarkzx/Reaper-MCP)**
(Apache-2.0): Provided the concept of exposing a large tool surface (composition, mixing, mastering, QC, ReaScript automation) to the AI.

**[bonfire-systems/reaper-mcp](https://github.com/bonfire-systems/reaper-mcp)**
(MIT): Provided the original MCP server implementation and the initial set of tools.

**[DevWesC/Reaper-Claude-MCP](https://github.com/DevWesC/Reaper-Claude-MCP)**
(MIT): Provided the PowerShell installer approach for Windows.

Other dependencies:

**[python-reapy](https://github.com/RomeoDespres/reapy)** by Roméo Després: Provides the external API for REAPER.

**[Cockos](https://www.reaper.fm/)**: Developer of REAPER and the ReaScript API.

**[EXLOUD/winget-installer](https://github.com/EXLOUD/winget-installer)**: Documented the winget version pinning (1.8.1911) used in the offline installation script.

For licensing and third-party component details, see **[LEGAL.md](LEGAL.md)**.
