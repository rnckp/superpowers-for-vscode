#!/usr/bin/env python3
# /// script
# ///
"""Sync Superpowers skills into a project for VS Code Copilot.

Run from the superpowers-vscode repo root. Generates into the target project:
- .github/copilot-instructions.md (bootstrap, auto-loaded by VS Code)
- .github/prompts/*.prompt.md (one per skill, invokable with # in chat)
- .superpowers/skills/<name>/* (supporting files: prompts, scripts, guides)
- .superpowers/agents/* (agent definitions)

Usage:
    uv run scripts/sync-skills.py --target /path/to/project
    uv run scripts/sync-skills.py --global

    The script auto-detects the upstream Superpowers repo as a sibling
    directory. Override with SUPERPOWERS_REPO env var or --source.

Examples:
    # Minimal — auto-detect source repo:
    uv run scripts/sync-skills.py --target ~/projects/my-app

    # Explicit source and target:
    uv run scripts/sync-skills.py --source /path/to/superpowers --target /path/to/project

    # Global install — user-level prompts available in all workspaces:
    uv run scripts/sync-skills.py --global
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

# Skill directory name -> prompt file name
SKILL_NAMES: dict[str, str] = {
    "brainstorming": "superpowers-brainstorming",
    "test-driven-development": "superpowers-tdd",
    "systematic-debugging": "superpowers-debugging",
    "verification-before-completion": "superpowers-verification",
    "executing-plans": "superpowers-executing-plans",
    "writing-plans": "superpowers-writing-plans",
    "subagent-driven-development": "superpowers-subagent-dev",
    "dispatching-parallel-agents": "superpowers-parallel-agents",
    "finishing-a-development-branch": "superpowers-finish-branch",
    "requesting-code-review": "superpowers-request-review",
    "receiving-code-review": "superpowers-receive-review",
    "using-git-worktrees": "superpowers-git-worktrees",
    "writing-skills": "superpowers-writing-skills",
}

# Files to skip when copying supporting files (dev artifacts, test files)
SKIP_PATTERNS = {
    "CREATION-LOG.md",
    "test-academic.md",
    "test-pressure-1.md",
    "test-pressure-2.md",
    "test-pressure-3.md",
}

# Relative path prefix for supporting files inside the target project
SUPERPOWERS_DIR = ".superpowers"

TOOL_MAPPING_FOOTER_BASE = """\

---

## VS Code Copilot — Tool Mapping

When this skill references Claude Code tools, use VS Code Copilot equivalents:

| Skill says | You use |
|------------|---------|
| `Read` | `read_file` |
| `Write` | `create_file` |
| `Edit` | `apply_patch` |
| `Bash` | `run_in_terminal` or `execution_subagent` |
| `Grep` | `grep_search` |
| `Glob` | `file_search` |
| `Task` (subagent) | `runSubagent` (sequential only — no parallel dispatch) |
| `TodoWrite` | `manage_todo_list` |
| `WebFetch` | `fetch_webpage` |
| `Skill` tool | No tool equivalent — skills are prompt files, reference with `#` in chat |

### VS Code Copilot Notes

{path_note}\
- `runSubagent` calls must be sequential. For parallel agent skills, execute tasks one at a time.
- `manage_todo_list` states: `not-started`, `in-progress` (max 1), `completed`.
- `apply_patch` edits text files using a unified diff with surrounding context.
- Named agents (e.g., `superpowers:code-reviewer`): read the agent prompt file, then use `runSubagent` with the content.
"""

_PATH_NOTE_PROJECT = (
    "- **`.superpowers/` paths are relative to the workspace root directory** "
    "(NOT relative to this prompt file). For example, "
    "`.superpowers/skills/brainstorming/visual-companion.md` means "
    "`<workspace-root>/.superpowers/skills/brainstorming/visual-companion.md`. "
    "Always use `read_file` with the exact path shown. "
    "Do NOT use `file_search` — these files may be gitignored.\n"
)

_PATH_NOTE_GLOBAL = (
    "- Supporting file paths in this skill are **absolute paths**. "
    "Use `read_file` with the exact paths shown.\n"
)


def get_tool_mapping_footer(*, is_global: bool = False) -> str:
    """Return the tool mapping footer with mode-appropriate path notes."""
    note = _PATH_NOTE_GLOBAL if is_global else _PATH_NOTE_PROJECT
    return TOOL_MAPPING_FOOTER_BASE.format(path_note=note)


def get_vscode_user_prompts_dir() -> Path:
    """Return the VS Code user-level prompts directory for the current platform."""
    system = platform.system()
    if system == "Darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Code"
            / "User"
            / "prompts"
        )
    elif system == "Linux":
        return (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "Code"
            / "User"
            / "prompts"
        )
    elif system == "Windows":
        return (
            Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
            / "Code"
            / "User"
            / "prompts"
        )
    else:
        print(f"Error: unsupported platform: {system}")
        sys.exit(1)


def parse_args() -> tuple[Path, Path, bool]:
    """Parse command-line arguments, returning (source_repo, target_dir, is_global)."""
    source = None
    target = None
    is_global = False
    positional_args: list[str] = []

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--source" and i + 1 < len(args):
            source = args[i + 1]
            i += 2
        elif args[i] == "--target" and i + 1 < len(args):
            target = args[i + 1]
            i += 2
        elif args[i] == "--global":
            is_global = True
            i += 1
        elif args[i] in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        else:
            positional_args.append(args[i])
            i += 1

    if positional_args:
        if (
            source is not None
            or target is not None
            or is_global
            or len(positional_args) > 1
        ):
            print(f"Error: unexpected extra arguments: {' '.join(positional_args)}")
            print()
            print("If a path contains spaces, quote it:")
            print(
                '  uv run scripts/sync-skills.py --target "/path/with spaces/project"'
            )
            print(
                '  uv run scripts/sync-skills.py --source "/path/with spaces/superpowers" --target "/path/with spaces/project"'
            )
            sys.exit(1)

        # Legacy: a single positional arg is the source path.
        source = positional_args[0]

    # Resolve source (upstream superpowers repo)
    env_source = os.environ.get("SUPERPOWERS_REPO")
    source_repo = find_superpowers_repo(env_source or source)

    if is_global:
        # Global mode: use ~/.superpowers as target for supporting files
        target_dir = (Path.home() / ".superpowers").resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        return source_repo, target_dir, True

    # Resolve target (required for project mode)
    if not target:
        print("Error: --target or --global is required.")
        print("Usage: uv run scripts/sync-skills.py --target /path/to/project")
        print("       uv run scripts/sync-skills.py --global")
        sys.exit(1)
    target_dir = Path(target).resolve()
    if not target_dir.is_dir():
        print(f"Error: target directory does not exist: {target_dir}")
        sys.exit(1)

    return source_repo, target_dir, False


def find_superpowers_repo(explicit_path: Optional[str] = None) -> Path:
    """Find the superpowers repo, checking multiple locations."""
    if explicit_path:
        p = Path(explicit_path).expanduser()
        if (p / "skills").is_dir():
            return p.resolve()
        print(f"Error: {explicit_path} does not contain a skills/ directory")
        sys.exit(1)

    script_dir = Path(__file__).resolve().parent
    adapter_root = script_dir.parent

    candidates = [
        adapter_root / ".." / "superpowers",
        adapter_root / ".." / ".." / "_ GitHub generell" / "superpowers",
        Path.home() / "superpowers",
    ]

    for candidate in candidates:
        if (candidate / "skills").is_dir():
            return candidate.resolve()

    print("Error: Cannot find superpowers repo.")
    print("Set SUPERPOWERS_REPO env var or use --source /path/to/superpowers")
    sys.exit(1)


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter and return (metadata, body)."""
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    metadata: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            value = value.strip().strip('"').strip("'")
            metadata[key.strip()] = value

    body = parts[2].lstrip("\n")
    return metadata, body


def copy_supporting_files(src_skill_dir: Path, dst_skill_dir: Path) -> list[str]:
    """Copy non-SKILL.md files from a skill directory. Returns list of relative paths."""
    copied = []
    for src_file in sorted(src_skill_dir.rglob("*")):
        if not src_file.is_file():
            continue
        if src_file.name == "SKILL.md":
            continue
        if src_file.name in SKIP_PATTERNS:
            continue

        rel = src_file.relative_to(src_skill_dir)
        dst = dst_skill_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst)
        copied.append(str(rel))

    return copied


def inline_at_references(body: str, skill_dir: Path) -> str:
    """Replace @filename references with inline content.

    Claude Code's @ syntax force-loads files into context. Since VS Code
    has no equivalent, we inline the content directly.
    """

    def replace_at_ref(match: re.Match) -> str:
        prefix = match.group(1)
        filename = match.group(2)
        filepath = skill_dir / filename
        if filepath.exists():
            content = filepath.read_text().rstrip()
            return f"{prefix}the content of `{filename}` (inlined below):\n\n{content}"
        return match.group(0)

    body = re.sub(
        r"((?:See |see |See also |\s)?)@([a-zA-Z0-9_-]+\.[a-zA-Z0-9]+)",
        replace_at_ref,
        body,
    )
    return body


def rewrite_relative_refs(
    body: str,
    skill_name: str,
    supporting_files: list[str] | None = None,
    *,
    absolute_prefix: str | None = None,
) -> str:
    """Rewrite file references to .superpowers/skills/<name>/filename paths.

    In project mode (default), uses workspace-root-relative paths under
    .superpowers/ annotated with '(workspace root)'. In global mode
    (absolute_prefix set), uses absolute paths.
    """
    if absolute_prefix:
        prefix = f"{absolute_prefix}/skills/{skill_name}"
        annotation = ""
    else:
        prefix = f"{SUPERPOWERS_DIR}/skills/{skill_name}"
        annotation = " (workspace root)"

    # `./filename.md` -> `.superpowers/skills/<skill_name>/filename.md`
    body = re.sub(
        r"`\./([^`]+)`",
        lambda m: f"`{prefix}/{m.group(1)}`{annotation}",
        body,
    )
    # (./filename.md) in graphviz labels etc.
    body = re.sub(
        r"\(\./([^)]+)\)",
        lambda m: f"({prefix}/{m.group(1)})",
        body,
    )
    # `skills/<name>/filename` -> `.superpowers/skills/<name>/filename`
    body = re.sub(
        r"`skills/([^`]+)`",
        lambda m: (
            f"`{absolute_prefix or SUPERPOWERS_DIR}/skills/{m.group(1)}`{annotation}"
        ),
        body,
    )

    # Bare `filename.ext` references that match actual supporting files
    if supporting_files:
        filenames = {Path(f).name for f in supporting_files}

        def rewrite_bare_ref(m: re.Match) -> str:
            filename = m.group(1)
            if filename in filenames:
                return f"`{prefix}/{filename}`{annotation}"
            return m.group(0)

        body = re.sub(r"`([a-zA-Z0-9_-]+\.[a-zA-Z0-9]+)`", rewrite_bare_ref, body)

    # Plain text `<skill-name>/filename` references (e.g., "See template at: ...")
    body = re.sub(
        rf"(?<=\s){re.escape(skill_name)}/([a-zA-Z0-9_.-]+\.md)",
        lambda m: f"`{prefix}/{m.group(1)}`{annotation}",
        body,
    )

    return body


def build_supporting_files_note(
    skill_name: str, files: list[str], *, absolute_prefix: str | None = None
) -> str:
    """Generate a note about available supporting files for a prompt."""
    if not files:
        return ""

    if absolute_prefix:
        prefix = f"{absolute_prefix}/skills/{skill_name}"
        lines = [
            "\n\n---\n",
            "## Supporting Files\n",
            "This skill has supporting files. Use `read_file` to access them:\n",
        ]
    else:
        prefix = f"{SUPERPOWERS_DIR}/skills/{skill_name}"
        lines = [
            "\n\n---\n",
            "## Supporting Files\n",
            "This skill has supporting files in the **workspace root** directory.\n",
            "Use `read_file` with these exact paths (relative to workspace root, do NOT use `file_search`):\n",
        ]
    for f in sorted(files):
        lines.append(f"- `{prefix}/{f}`")

    return "\n".join(lines) + "\n"


def generate_prompt_file(
    skill_name: str,
    metadata: dict[str, str],
    body: str,
    supporting_files: list[str],
    *,
    absolute_prefix: str | None = None,
) -> str:
    """Generate a VS Code prompt file from skill content."""
    description = metadata.get("description", f"Superpowers {skill_name} skill")
    description = description.replace('"', '\\"')

    supporting_note = build_supporting_files_note(
        skill_name, supporting_files, absolute_prefix=absolute_prefix
    )

    is_global = absolute_prefix is not None
    footer = get_tool_mapping_footer(is_global=is_global)

    return f"""\
---
mode: agent
description: "{description}"
---

<!-- AUTO-GENERATED from superpowers/{skill_name}/SKILL.md -->
<!-- Do not edit manually. Run: uv run scripts/sync-skills.py -->

{body.rstrip()}
{supporting_note}{footer}"""


def generate_bootstrap(
    using_superpowers_body: str, skill_list: list[tuple[str, str, str]]
) -> str:
    """Generate copilot-instructions.md from using-superpowers skill content."""
    skill_rows = []
    for skill_name, prompt_name, description in sorted(skill_list, key=lambda x: x[0]):
        short_desc = description[:80] + "..." if len(description) > 80 else description
        skill_rows.append(f"| {skill_name} | `#{prompt_name}` | {short_desc} |")
    skill_table = "\n".join(skill_rows)

    # Remove the multi-platform "How to Access Skills" and "Platform Adaptation"
    # sections -- replaced by VS Code-specific instructions in the template below
    adapted_body = using_superpowers_body
    access_section_start = "## How to Access Skills"
    access_section_end = "## Platform Adaptation"

    if access_section_start in adapted_body and access_section_end in adapted_body:
        before = adapted_body[: adapted_body.index(access_section_start)]
        after = adapted_body[adapted_body.index(access_section_end) :]
        platform_end_markers = ["\n# Using Skills", "\n## Using Skills"]
        for marker in platform_end_markers:
            if marker in after:
                after = after[after.index(marker) :]
                break
        adapted_body = before + after
    elif access_section_start in adapted_body:
        before = adapted_body[: adapted_body.index(access_section_start)]
        rest = adapted_body[adapted_body.index(access_section_start) :]
        lines = rest.split("\n")
        end_idx = len(lines)
        for i, line in enumerate(lines[1:], 1):
            if line.startswith("## ") or line.startswith("# "):
                end_idx = i
                break
        after = "\n".join(lines[end_idx:])
        adapted_body = before + after

    return f"""\
<!-- AUTO-GENERATED from Superpowers — do not edit manually. -->
<!-- Run: uv run scripts/sync-skills.py -->
<!-- Source: https://github.com/obra/superpowers -->

# Superpowers — VS Code Copilot

You have superpowers. You have access to a curated skills library that enforces
disciplined software development workflows.

## Platform: VS Code with GitHub Copilot

You are running in VS Code with GitHub Copilot. Skills use Claude Code tool
names — translate them using this mapping:

| Skill says | You use |
|------------|---------|
| `Read` | `read_file` |
| `Write` | `create_file` |
| `Edit` | `apply_patch` |
| `Bash` | `run_in_terminal` or `execution_subagent` |
| `Grep` | `grep_search` |
| `Glob` | `file_search` |
| `Task` (subagent) | `runSubagent` (sequential only — no parallel dispatch) |
| `TodoWrite` | `manage_todo_list` |
| `WebFetch` | `fetch_webpage` |
| `Skill` tool | **No tool equivalent** — see below |

**Important:** All `.superpowers/` paths in skills are **relative to the workspace root directory**, not to the prompt file location. Use `read_file` with the exact paths shown — do NOT use `file_search` to locate them, as gitignored files are invisible to search.

## How to Access Skills

VS Code Copilot has no `Skill` tool. Skills are available as **prompt files**.

When a skill applies to the current task:
1. **Tell your human partner** which skill to reference
   (e.g., "This looks like a debugging task — please add `#superpowers-debugging` to your message")
2. Your human partner adds the prompt file reference in chat with `#`
3. Follow the loaded skill content exactly

### Available Skills

| Skill | Prompt file | Description |
|-------|-------------|-------------|
{skill_table}

## Core Behavior

{adapted_body.rstrip()}
"""


def main() -> None:
    source_repo, target_dir, is_global = parse_args()

    skills_dir = source_repo / "skills"
    agents_src = source_repo / "agents"

    # Determine output directories based on mode
    if is_global:
        prompts_dir = get_vscode_user_prompts_dir()
        prompts_dir.mkdir(parents=True, exist_ok=True)
        # Supporting files go to ~/.superpowers/
        superpowers_dir = target_dir  # already ~/.superpowers
        skills_out = superpowers_dir / "skills"
        agents_out = superpowers_dir / "agents"
        # Absolute path prefix for prompt file references
        abs_prefix = str(superpowers_dir)
    else:
        prompts_dir = target_dir / ".github" / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        superpowers_dir = target_dir / SUPERPOWERS_DIR
        skills_out = superpowers_dir / "skills"
        agents_out = superpowers_dir / "agents"
        abs_prefix = None

    # Read version
    version = "unknown"
    version_file = source_repo / "package.json"
    if version_file.exists():
        pkg = json.loads(version_file.read_text())
        version = pkg.get("version", "unknown")

    print(f"Source:  {source_repo} (v{version})")
    if is_global:
        print(f"Mode:    global")
        print(f"Prompts: {prompts_dir}")
        print(f"Files:   {superpowers_dir}")
    else:
        print(f"Target:  {target_dir}")
    print()

    # Process using-superpowers (bootstrap) — project mode only
    using_sp = skills_dir / "using-superpowers" / "SKILL.md"
    if not using_sp.exists():
        print("Error: using-superpowers/SKILL.md not found")
        sys.exit(1)
    _, using_sp_body = parse_frontmatter(using_sp.read_text())

    # Process all other skills
    skill_list: list[tuple[str, str, str]] = []
    generated_count = 0
    supporting_count = 0

    for skill_path in sorted(skills_dir.iterdir()):
        if not skill_path.is_dir():
            continue

        skill_name = skill_path.name
        if skill_name == "using-superpowers":
            continue

        skill_file = skill_path / "SKILL.md"
        if not skill_file.exists():
            print(f"  ⚠ {skill_name}: no SKILL.md, skipping")
            continue

        prompt_name = SKILL_NAMES.get(skill_name, f"superpowers-{skill_name}")
        metadata, body = parse_frontmatter(skill_file.read_text())
        description = metadata.get("description", f"Superpowers {skill_name} skill")

        # Copy supporting files
        dst_skill_dir = skills_out / skill_name
        if dst_skill_dir.exists():
            shutil.rmtree(dst_skill_dir)
        copied_files = copy_supporting_files(skill_path, dst_skill_dir)
        if copied_files:
            supporting_count += len(copied_files)
            label = str(superpowers_dir) if is_global else f"{SUPERPOWERS_DIR}"
            print(f"  ✓ {label}/skills/{skill_name}/ ({len(copied_files)} files)")

        # Transform skill body
        body = inline_at_references(body, skill_path)
        body = rewrite_relative_refs(
            body, skill_name, copied_files, absolute_prefix=abs_prefix
        )

        skill_list.append((skill_name, prompt_name, description))

        # Generate prompt file
        prompt_content = generate_prompt_file(
            skill_name, metadata, body, copied_files, absolute_prefix=abs_prefix
        )
        prompt_path = prompts_dir / f"{prompt_name}.prompt.md"
        prompt_path.write_text(prompt_content)
        print(f"  ✓ {prompt_name}.prompt.md")
        generated_count += 1

    # Copy agents/
    if agents_src.is_dir():
        if agents_out.exists():
            shutil.rmtree(agents_out)
        shutil.copytree(agents_src, agents_out)
        agent_files = list(agents_out.rglob("*.md"))
        print(f"  ✓ agents/ ({len(agent_files)} agents)")

    # Generate bootstrap — project mode only
    if not is_global:
        bootstrap = generate_bootstrap(using_sp_body, skill_list)
        instructions_dir = target_dir / ".github"
        instructions_dir.mkdir(parents=True, exist_ok=True)
        instructions_path = instructions_dir / "copilot-instructions.md"
        instructions_path.write_text(bootstrap)
        print(f"  ✓ .github/copilot-instructions.md")

    print()
    print(
        f"Done — {generated_count} prompt files + {supporting_count} supporting files (Superpowers v{version})"
    )

    if is_global:
        print()
        print("Global install complete. Skills available via # in any workspace.")
        print(f"  Prompts:          {prompts_dir}/superpowers-*.prompt.md")
        print(f"  Supporting files: {superpowers_dir}/")
        print()
        print("Note: copilot-instructions.md (bootstrap) is per-project.")
        print("Run with --target for project-level bootstrap + prompts.")
    else:
        print()
        print("Generated files (can be committed or gitignored):")
        print(f"  .github/copilot-instructions.md")
        print(f"  .github/prompts/superpowers-*.prompt.md")
        print(f"  {SUPERPOWERS_DIR}/")


if __name__ == "__main__":
    main()
