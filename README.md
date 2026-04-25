# Superpowers for VS Code

VS Code Copilot adapter for [Superpowers](https://github.com/obra/superpowers) — an agentic skill framework and software development methodology.

Superpowers ships with support for Claude Code, Cursor, Codex, Gemini CLI, and Copilot CLI. This adapter bridges it to **VS Code + GitHub Copilot** by converting skills into Copilot-native prompt files and generating a bootstrap via `copilot-instructions.md`.

## Credits

All credits go to [Jesse Vincent](https://github.com/obra) for his excellent skill framework. 🙏

## How It Works

A **sync script** reads from the upstream Superpowers repo and generates files directly into your target project:

- `.github/copilot-instructions.md` — auto-loaded bootstrap with tool mapping and skill index
- `.github/prompts/*.prompt.md` — one prompt file per skill, invokable with `/` in chat
- `.superpowers/skills/<name>/*` — supporting files (subagent prompts, technique guides, scripts)
- `.superpowers/agents/*` — agent definitions (e.g., code-reviewer)
- `@filename` references are inlined, `./path` references are rewritten to `.superpowers/` paths annotated with `(workspace root)` so models resolve them correctly

The upstream repo is **never modified** — updates are a `git pull` + re-sync away.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- A local clone of the [Superpowers repo](https://github.com/obra/superpowers)

## Setup

```bash
# 1. Clone both repos
git clone https://github.com/obra/superpowers
git clone <this-repo> superpowers-vscode

# 2. Sync skills into your project
cd superpowers-vscode
uv run scripts/sync-skills.py --target /path/to/your-project
```

The script auto-detects a sibling `superpowers/` directory as the source. Override with `--source` or `SUPERPOWERS_REPO` env var. If either path contains spaces, wrap it in quotes.

### Per-Project Install (recommended)

```bash
# Auto-detect source, explicit target
uv run scripts/sync-skills.py --target /path/to/your-project

# Explicit source and target
uv run scripts/sync-skills.py --source /path/to/superpowers --target /path/to/your-project
```

Generated files use workspace-relative paths, so they work portably across machines.

**Commit or gitignore** — the generated files are self-contained. Add them to your project's `.gitignore` if you prefer re-generating on each machine:

```gitignore
.superpowers/
.github/copilot-instructions.md
.github/prompts/superpowers-*.prompt.md
```

### Global Install

Install skills as user-level prompts, available in **all workspaces** without per-project setup:

```bash
uv run scripts/sync-skills.py --global
```

This generates:

- Prompt files → VS Code user prompts directory (platform-specific)
- Supporting files → `~/.superpowers/`
- Paths are absolute, so files resolve correctly in any workspace

**Note:** `copilot-instructions.md` (the bootstrap that auto-suggests skills) is per-project only. Use `--target` for the full experience, or `--global` for quick access without per-project setup.

## Usage

In any Copilot chat, reference skills with `/`:

| Prompt file                    | When to use                  |
| ------------------------------ | ---------------------------- |
| `/superpowers-brainstorming`   | Before creative/design work  |
| `/superpowers-tdd`             | Test-driven development      |
| `/superpowers-debugging`       | Systematic bug investigation |
| `/superpowers-verification`    | Before claiming work is done |
| `/superpowers-writing-plans`   | Breaking designs into tasks  |
| `/superpowers-executing-plans` | Step-by-step plan execution  |
| `/superpowers-subagent-dev`    | Complex multi-task work      |
| `/superpowers-parallel-agents` | Independent parallel tasks   |
| `/superpowers-finish-branch`   | Branch completion and merge  |
| `/superpowers-git-worktrees`   | Isolated workspaces          |
| `/superpowers-request-review`  | Pre-merge review dispatch    |
| `/superpowers-receive-review`  | Handling review feedback     |
| `/superpowers-writing-skills`  | Creating new skills          |

The bootstrap instructs Copilot to suggest relevant skills — it will tell you which `/superpowers-*` prompt command to run.

## Updating

When the upstream Superpowers repo releases a new version:

```bash
cd /path/to/superpowers && git pull
cd /path/to/superpowers-vscode
uv run scripts/sync-skills.py --target /path/to/your-project  # or --global
```

## Tool Mapping

Skills are authored with Claude Code tool names. The bootstrap and each prompt file include a mapping table. Key translations:

| Skill says  | Copilot uses                             |
| ----------- | ---------------------------------------- |
| `Read`      | `read_file`                              |
| `Write`     | `create_file`                            |
| `Edit`      | `apply_patch`                            |
| `Bash`      | `run_in_terminal` / `execution_subagent` |
| `Grep`      | `grep_search`                            |
| `Glob`      | `file_search`                            |
| `Task`      | `runSubagent` (sequential)               |
| `TodoWrite` | `manage_todo_list`                       |
| `Skill`     | Prompt files via `/`                     |

Full mapping: [references/vscode-copilot-tools.md](references/vscode-copilot-tools.md)

## Project Structure

```
superpowers-vscode/              # This adapter repo
├── scripts/
│   └── sync-skills.py           # Sync script (the only essential file)

your-project/                    # Target project (after sync)
├── .github/
│   ├── copilot-instructions.md  # Auto-loaded bootstrap (generated)
│   └── prompts/                 # Skill prompt files (generated)
│       └── superpowers-*.prompt.md
└── .superpowers/                # Supporting files (generated)
    ├── agents/                  # Agent definitions
    └── skills/                  # Per-skill supporting files
        ├── brainstorming/       #   scripts, visual-companion, prompts
        ├── systematic-debugging/#   technique guides, scripts
        └── ...                  #   (only skills with extras)
```

## License

MIT — same as [Superpowers](https://github.com/obra/superpowers).
