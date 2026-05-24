# RepOS (repos-cli)

[![PyPI version](https://img.shields.io/pypi/v/repos-cli.svg)](https://pypi.org/project/repos-cli/)
[![CI](https://github.com/trifactoria/repos-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/trifactoria/repos-cli/actions/workflows/ci.yml)
[![Python versions](https://img.shields.io/pypi/pyversions/repos-cli.svg)](https://pypi.org/project/repos-cli/)
[![License: BSL 1.1](https://img.shields.io/badge/license-BSL%201.1-blue.svg)](LICENSE)

**RepOS** is a local-first Python CLI for organizing repeatable shell workflows into persistent REPL panels. It runs on top of your existing shell, stores aliases and command history in SQLite, and lets you move between contexts such as Git, Docker, Python, Node, Conda, Ruby, and OS tasks without growing a pile of one-off scripts.

This package installs the **RepOS CLI**.

## Why this exists

Developer command workflows often start as a few shell aliases, then spread across dotfiles, project notes, snippets, and muscle memory. RepOS keeps those workflows local and inspectable while giving them a small amount of structure: panels for context, SQLite persistence for aliases and history, and YAML defaults for common toolchains.

RepOS is not meant to replace Bash, Zsh, Fish, or your terminal. It is a command layer that helps organize and rerun the shell commands you already use.

## Installation

```bash
pip install repos-cli
```

Python 3.10+ required.

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

You’ll enter the root **REP** panel. This takes no other arguments and creates a `.repos` file in the current directory.

Exit at any time with:

```text
ZZ
```

## Example session

This transcript shows the basic flow: start in `REP`, switch to a tool panel, inspect aliases, run an alias, add a project-specific alias, and exit.

```text
$ repos-cli
REP> G
G> L
[G] aliases:
  - s -> git status
  - l -> git log --oneline --decorate --graph -n 25
  ...
G> s
[RUN] git status
...
G> N today git status --short
Added alias 'today' in panel G.
G> today
[RUN] git status --short
...
G> Z
REP> ZZ
```

## What RepOS Does

- Provides a **panel-based REPL** inside your terminal
- Stores aliases persistently using SQLite
- Executes real shell commands (not simulations)
- Tracks command history and execution results
- Uses YAML configuration for system and panel defaults

RepOS is **not a shell** and does not replace Bash/Zsh. It runs _on top of_ your existing shell.

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

## Architecture

RepOS is intentionally small and local:

- **CLI entrypoint**: `repos_cli.cli:main` starts the process, resolves the active database, wires the store, executor, kernel, and UI, then runs the REPL loop.
- **Kernel**: `repos_cli.kernel.Kernel` owns session state, panel navigation, built-in commands, alias expansion, history recording, and command dispatch.
- **Executor**: `repos_cli.executor.SubprocessExecutor` runs resolved commands through the local system. It supports captured output for normal commands and TTY passthrough for interactive tools configured in YAML.
- **SQLite store**: `repos_cli.store.SQLiteStore` persists aliases, settings, command history, and execution details in local SQLite databases initialized by `repos_cli.db`.
- **prompt-toolkit UI**: `repos_cli.ui.PromptToolkitUI` provides terminal input, completions, panel display, clear handling, and a more usable interactive prompt.
- **YAML defaults**: packaged files under `repos_cli/defaults/` define system behavior, panels, branding, and starter aliases for Git, Python, Node, Docker, Conda, Ruby, and OS tasks.

## Safety model and limitations

RepOS executes commands locally for real. It is not a sandbox, simulator, permission boundary, or security wrapper.

- Aliases expand to shell commands and run on your machine with your user permissions.
- Commands can modify files, call network tools, start processes, use credentials available in your environment, or run destructive operations if you configure them that way.
- RepOS records command history and execution output in local SQLite databases, subject to the truncation and logging behavior implemented by the CLI.
- TTY passthrough is used for interactive commands such as editors, pagers, shells, and selected Git commands.
- Review aliases before running them, especially aliases copied from another machine or project.

Use RepOS as a local workflow organizer, not as a safety layer around untrusted commands.

## Project status and roadmap

RepOS is an **early-stage release** focused on the core REPL, execution engine, prompt UI, SQLite persistence model, packaged YAML defaults, and PyPI distribution. The interface and internal architecture are still evolving.

Current focus:

- Stabilize core command and alias behavior.
- Improve documentation for panels, alias syntax, configuration, and project databases.
- Expand test coverage around interactive workflows and edge cases.
- Keep packaging and CI reliable as the project changes.

Not currently promised:

- Remote sync.
- Multi-user hosting.
- Command sandboxing.
- Replacement of a full shell environment.

## Licensing

RepOS is source-available under the **Business Source License 1.1 (BSL 1.1)**.

In plain English:

- You can use, modify, and redistribute the code for personal use and internal development.
- You cannot sell RepOS itself or offer it as a hosted/commercial service before the Change Date without a commercial license.
- On January 1, 2029, the license automatically converts to **Apache License 2.0**.

See:

- `LICENSE`
- `NOTICE`
- `legal/` directory for commercial licensing and contribution terms

RepOS™ and TriFactoria™ are trademarks of Andrew Blankfield.

## Support

If you find RepOS useful and would like to support its continued development, donations are welcome.

GitHub Sponsors: https://github.com/sponsors/trifactoria
