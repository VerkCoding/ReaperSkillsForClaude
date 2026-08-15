# Legal

Licence, attribution, and exactly what the installer fetches and touches.

- [Licence](#licence)
- [Third-party components](#third-party-components)
- [What the installer downloads](#what-the-installer-downloads)
- [What it reads on your machine](#what-it-reads-on-your-machine)
- [Trademarks](#trademarks)
- [No warranty](#no-warranty)

---

## Licence

This project is released under the **MIT Licence**. The full text is in
[LICENSE](LICENSE).

> [!IMPORTANT]
> **The copyright line needs a decision before publishing.**
>
> `LICENSE` currently reads `Copyright (c) 2025 Youssef Hemimy`, while
> `.claude-plugin/plugin.json` names a different author. That line was carried
> over from [bonfire-systems/reaper-mcp](https://github.com/bonfire-systems/reaper-mcp),
> which this project derives from.
>
> MIT requires the original notice to be kept in derivative works, so removing
> it is not an option. The usual practice is to keep it **and add your own**:
>
> ```
> Copyright (c) 2025 Youssef Hemimy
> Copyright (c) 2026 <your name>
> ```
>
> This is deliberately left as-is rather than changed on your behalf. Who holds
> copyright is not a decision for a tool to make.

## Third-party components

### Derived from: code

| Project | Licence | Relationship |
| --- | --- | --- |
| [bonfire-systems/reaper-mcp](https://github.com/bonfire-systems/reaper-mcp) | MIT | The MCP server here is derived from it. |

MIT requires its copyright notice to be kept in derivative works, which is why
the line discussed [above](#licence) stays in `LICENSE`.

### Acknowledged influence, no code taken

Credited in the [shout-outs](README.md#shout-outs). Listed here so the boundary
is on the record: these shaped the design, and no source was copied from either.

| Project | Licence | Relationship |
| --- | --- | --- |
| [xDarkzx/Reaper-MCP](https://github.com/xDarkzx/Reaper-MCP) | Apache-2.0 | Ideology: the case for a broad tool surface rather than a few calls. |
| [DevWesC/Reaper-Claude-MCP](https://github.com/DevWesC/Reaper-Claude-MCP) | MIT | Inspiration: the Claude-and-REAPER pairing, and installing it with PowerShell. |

Ideas are not copyrightable, so influence alone carries no licence obligation
and neither project's terms attach to this one. Worth knowing where that line
is, though: **Apache-2.0 is stricter than MIT** about reuse. If code were ever
taken from xDarkzx/Reaper-MCP, that would additionally require preserving its
`NOTICE` file and stating what was changed. Nothing here does, and this section
should be revisited if that ever stops being true.

### Runtime dependencies

Installed into a virtualenv at `%USERPROFILE%\.reaper-for-claude\venv`, not
vendored into this repository. Each keeps its own licence.

| Package | Licence |
| --- | --- |
| [python-reapy](https://github.com/RomeoDespres/reapy) | MIT |
| [mcp](https://github.com/modelcontextprotocol/python-sdk) (pinned `<2.0.0`) | MIT |
| [numpy](https://numpy.org/) | BSD-3-Clause |
| [librosa](https://librosa.org/) | ISC |
| [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm) | MIT |
| [soundfile](https://github.com/bastibe/python-soundfile) | BSD-3-Clause |

`librosa` pulls in `numba`, `llvmlite`, `scipy` and `scikit-learn` transitively
(BSD-family licences). Run `pip list` inside the virtualenv for the resolved set
on your machine.

**`python-reapy` is also installed into your base Python's user site-packages.**
This is the one thing that lands outside the virtualenv, and it is required:
REAPER embeds a Python interpreter and cannot see a virtualenv. See
[Two interpreters](DOCS.md#two-interpreters).

## What the installer downloads

`[1] Install Everything` fetches from these hosts and no others. Everything is
cached in `downloadCache\`, so you can inspect it before it is used, and `[3]
Prepare Offline Files` fetches the same set without installing anything.

| What | From |
| --- | --- |
| Python, Git, REAPER, Claude Desktop, Claude Code | the **winget community source**, which resolves to each vendor's own installer: `python.org`, `git-scm.com`, `reaper.fm`, `claude.ai` |
| winget itself, if absent | `github.com/microsoft/winget-cli` releases |
| UI.Xaml | `github.com/microsoft/microsoft-ui-xaml` releases |
| VCLibs | `aka.ms` → `download.microsoft.com` |
| Python packages | PyPI, via pip |

**Nothing is fetched from a third-party mirror.** An earlier version used one
and it was removed; see the note in `install/lib-download-cache.ps1`.

Two safeguards on every download:

- **`curl --fail`**, so an HTTP error cannot be written to the output file and
  reported as a success.
- **A size floor**, which catches what `--fail` cannot. A captive portal's
  login page and a retired `aka.ms` link both answer `200` with something that
  is not the package.

Windows validates the MSIX signature chain when the winget packages are
deployed, so a package that is not the one Microsoft signed does not install.

## What it reads on your machine

The installer is invasive by nature. It has to be, to configure REAPER and
Claude. What it reads, and what it deliberately does not:

- **Claude's sign-in is checked for *presence only*.** The setup reads
  `config.json` to answer "is an account attached?", and it never reads, prints,
  stores or transmits a token value.
- **Nothing is sent anywhere.** There is no telemetry, no analytics, and no
  network call except the downloads listed above.
- **Logs stay local**, in `%USERPROFILE%\.reaper-for-claude\logs\`. They record
  what the setup did, and file paths. Read one before sharing it, as you would
  any log.
- **A snapshot is taken before the first write**, so `[2] Revert Everything` can
  undo it. See [What `[2]` restores](DOCS.md#what-2-restores).

REAPER project files, media, presets, FX chains, and Claude conversations are
never read, modified or deleted.

## Trademarks

**REAPER** is a trademark of **Cockos Incorporated**. **Claude** is a trademark
of **Anthropic PBC**. **Windows** is a trademark of **Microsoft Corporation**.

This project is an independent, unofficial integration. It is **not affiliated
with, endorsed by, or sponsored by** Cockos, Anthropic or Microsoft. Those names
are used only to identify the software this plugin works with.

You need your own licences for REAPER and Claude. This project provides neither.

## No warranty

MIT, in plain terms: this is provided **as is**, with **no warranty**, and the
authors are **not liable** for anything that comes of using it. See [LICENSE](LICENSE)
for the binding text.

Worth stating plainly, because this software edits configuration files that
matter:

- It writes to `reaper.ini`, `reaper-kb.ini`, `reaper-extstate.ini`,
  `__startup.lua` and `claude_desktop_config.json`.
- It takes a snapshot first and can restore it, and it guards `reaper.ini`
  against the known reapy failure that empties it. See the warning in
  [DOCS.md](DOCS.md#pinned-versions).
- Neither of those is a substitute for your own backup of a REAPER
  configuration you care about.
