# Legal

Information regarding licensing, credits, and liability.

## Licence

This project is released under the **MIT Licence**. The full text is available in [LICENSE](LICENSE).

The MCP server is derived from [bonfire-systems/reaper-mcp](https://github.com/bonfire-systems/reaper-mcp) (MIT). The `LICENSE` file includes their copyright notice as required by the MIT license, along with our copyright notice for subsequent additions.

## Credits

The following projects provided design concepts, but no code was used:
[xDarkzx/Reaper-MCP](https://github.com/xDarkzx/Reaper-MCP) (Apache-2.0) and
[DevWesC/Reaper-Claude-MCP](https://github.com/DevWesC/Reaper-Claude-MCP) (MIT).
Full acknowledgements are available in the [Credits and Dependencies](README.md#credits-and-dependencies) section of the README.

## Dependencies

Dependencies are installed into a virtual environment at `%USERPROFILE%\.reaper-for-claude\venv` and are not included in this repository. Each package retains its respective license.

| Package | Licence |
| --- | --- |
| [python-reapy](https://github.com/RomeoDespres/reapy) | MIT |
| [mcp](https://github.com/modelcontextprotocol/python-sdk) (pinned `<2.0.0`) | MIT |
| [numpy](https://numpy.org/) | BSD-3-Clause |
| [librosa](https://librosa.org/) | ISC |
| [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm) | MIT |
| [soundfile](https://github.com/bastibe/python-soundfile) | BSD-3-Clause |

`librosa` transitively installs `numba`, `llvmlite`, `scipy`, and `scikit-learn`, which use BSD-family licenses. Execute `pip list` within the virtual environment to view all installed packages.

The `python-reapy` package is additionally installed into the base Python user site-packages directory. REAPER utilizes an embedded interpreter that cannot access the virtual environment. See [Two interpreters](DOCS.md#two-interpreters).

## Installers

Downloads initiated by the setup script originate from the respective vendors: the winget community source for Python, Git, REAPER, and Claude; Microsoft for winget runtime components; and PyPI for Python packages. Third-party mirrors are not used.

Downloaded files are stored in `downloadCache\` prior to installation. The `[3] Prepare Offline Files` option downloads these files without executing the installation.

## Privacy

- No telemetry or analytics are collected. Network activity is limited to downloading the dependencies listed above.
- The setup script verifies the presence of a Claude sign-in. It does not read, store, or transmit authentication tokens.
- Log files are stored locally in `%USERPROFILE%\.reaper-for-claude\logs\`.
- The script does not read, modify, or delete REAPER projects, media files, presets, FX chains, or Claude conversations.

## Trademarks

**REAPER** is a trademark of **Cockos Incorporated**. **Claude** is a trademark of **Anthropic PBC**. **Windows** is a trademark of **Microsoft Corporation**.

This is an independent integration and is not affiliated with, endorsed by, or sponsored by these entities. Trademarks are used solely for identification. Users are responsible for acquiring their own licenses for REAPER and Claude.

## No warranty

This software is provided "as is", without warranty of any kind. The authors are not liable for any claim, damage, or other liability arising from its use. The [LICENSE](LICENSE) file contains the legally binding terms.

The setup script modifies `reaper.ini`, `reaper-kb.ini`, `reaper-extstate.ini`, `__startup.lua`, and `claude_desktop_config.json`. While the script creates a snapshot before modification and the `[2] Revert Everything` option can restore them, users should maintain their own independent backups of any critical REAPER configuration files.
