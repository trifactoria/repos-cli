# RepOS (repos-cli)
**RepOS** is a multi-panel, REPL-based command environment that runs inside your terminal.
It acts as a lightweight “operating layer” on top of your shell, letting you organize commands into panels (Git, OS, Python, Docker, etc.), store aliases persistently, and move between contexts without shell scripts or dotfile sprawl.
This package installs the **RepOS CLI**.

---
## Installation
```bash
pip install repos-cli
```
Python 3.10+ required.

---
## Usage
Start RepOS:
```bash
repos-cli
```
You’ll enter the root **REP** panel.

Create project level database:
```bash
repos-cli init
```
You’ll enter the root **REP** panel.  This takes no other arguments and will create a .repos file in the current directory.

Exit at any time with:
```text
ZZ
```
---
## What RepOS Does
- Provides a **panel-based REPL** inside your terminal
- Stores aliases persistently using SQLite
- Executes real shell commands (not simulations)
- Tracks command history and execution results
- Uses YAML configuration for system and panel defaults
RepOS is **not a shell** and does not replace Bash/Zsh — it runs _on top of_ your existing shell.
---
## Configuration & Defaults
On first run, RepOS initializes a local data directory under:
```
~/.local/share/repos/
```
This includes:
- a core SQLite database
- default panel definitions (Git, OS, Python, Node, Docker, etc.)
- system configuration loaded from packaged YAML defaults
You can customize panels and aliases from inside RepOS itself.
---
## Project Status
This is an **early-stage release** focused on establishing the core REPL, execution engine, and persistence model.
The interface and internal architecture are still evolving.

---
## Licensing
RepOS is licensed under the **Business Source License 1.1 (BSL 1.1)**.
- Free for personal use and internal development
- Not permitted to be sold or offered as a hosted service before the Change Date
- Automatically converts to **Apache License 2.0 on January 1, 2029**
See:
- `LICENSE`
- `NOTICE`
- `legal/` directory for commercial licensing and contribution terms
RepOS™ and TriFactoria™ are trademarks of Andrew Blankfield.

---
## Support
If you find RepOS useful and would like to support its continued development, donations are welcome.
GitHub Sponsors: https://github.com/sponsors/trifactoria