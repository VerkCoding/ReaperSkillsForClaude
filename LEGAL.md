# Legal

Licence, credits, and the limits of what this software promises.

## Licence

Released under the **MIT Licence**. Full text in [LICENSE](LICENSE).

The MCP server is derived from
[bonfire-systems/reaper-mcp](https://github.com/bonfire-systems/reaper-mcp)
(MIT), so `LICENSE` carries two copyright lines: theirs, which MIT requires a
derivative work to keep, and ours, which covers everything added since.

## Credits

Design and inspiration, no code taken:
[xDarkzx/Reaper-MCP](https://github.com/xDarkzx/Reaper-MCP) (Apache-2.0) and
[DevWesC/Reaper-Claude-MCP](https://github.com/DevWesC/Reaper-Claude-MCP) (MIT).
The full acknowledgements are in the [shout-outs](README.md#shout-outs).

## Dependencies

Installed into a virtualenv at `%USERPROFILE%\.reaper-for-claude\venv`, never
vendored into this repository. Each keeps its own licence.

| Package | Licence |
| --- | --- |
| [python-reapy](https://github.com/RomeoDespres/reapy) | MIT |
| [mcp](https://github.com/modelcontextprotocol/python-sdk) (pinned `<2.0.0`) | MIT |
| [numpy](https://numpy.org/) | BSD-3-Clause |
| [librosa](https://librosa.org/) | ISC |
| [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm) | MIT |
| [soundfile](https://github.com/bastibe/python-soundfile) | BSD-3-Clause |

`librosa` pulls in `numba`, `llvmlite`, `scipy` and `scikit-learn` transitively,
all BSD-family. Run `pip list` inside the virtualenv for the resolved set.

One package, `python-reapy`, is also installed into your base Python's user
site-packages. REAPER embeds its own interpreter and cannot see a virtualenv.
See [Two interpreters](DOCS.md#two-interpreters).

## Installers

Everything the setup downloads comes from the vendors themselves: the winget
community source for Python, Git, REAPER and Claude, Microsoft for the winget
runtime components, and PyPI for Python packages. No third-party mirrors.

Downloads land in `downloadCache\` before use, so you can inspect them.
`[3] Prepare Offline Files` fetches the same set without installing anything.

## Privacy

- **No telemetry, no analytics.** The only network traffic is the downloads
  above.
- **Claude's sign-in is checked for presence only.** The setup asks whether an
  account is attached. It never reads, stores or transmits a token.
- **Logs stay on your machine**, in `%USERPROFILE%\.reaper-for-claude\logs\`.
- **Your work is never touched.** REAPER projects, media, presets and FX chains,
  and Claude conversations, are not read, modified or deleted.

## Trademarks

**REAPER** is a trademark of **Cockos Incorporated**. **Claude** is a trademark
of **Anthropic PBC**. **Windows** is a trademark of **Microsoft Corporation**.

This is an independent, unofficial integration, **not affiliated with, endorsed
by, or sponsored by** any of them. Those names identify the software this plugin
works with, nothing more. You need your own licences for REAPER and Claude; this
project provides neither.

## No warranty

This software is provided **as is**, with **no warranty of any kind**, and the
authors are **not liable** for any claim, damage or other liability arising from
its use. [LICENSE](LICENSE) is the binding text.

Read that in light of what the setup does: it edits `reaper.ini`,
`reaper-kb.ini`, `reaper-extstate.ini`, `__startup.lua` and
`claude_desktop_config.json`. It snapshots them first and `[2] Revert
Everything` puts them back, but **a snapshot is not a backup**. Keep your own
copy of any REAPER configuration you would mind losing.
