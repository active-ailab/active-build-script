---
name: active-build
description: Use when the user asks an AI agent to build an Active/Zepp workspace with the active-build CLI, migrate from hmbuild.py usage, choose full/current build commands, or diagnose active-build command failures. This skill is only CLI usage guidance and must not reimplement build logic.
---

# active-build CLI Skill

Use this skill to operate the `active-build` CLI. The skill does not provide an independent build implementation; always call the installed CLI.

## Required Behavior

- Show the exact terminal command before running it.
- Run `active-build` from inside the target Active workspace or one of its subdirectories.
- Use the CLI as the source of truth for project discovery, manifest checks, defconfig restore behavior, and build command sequencing.
- Do not copy or reimplement `active-build` internals in agent code.
- On failure, report the command, working directory, exit code, and the most relevant error lines.
- For full builds, warn that the CLI may run `make`, temporarily edit and restore a defconfig, and ask interactive confirmation questions.

## Command Selection

Read `references/commands.md` when you need exact command patterns.

Common calls:

```sh
active-build
active-build <family> <project> <release|debug|sim> [threads]
active-build -c <app|ota> [threads]
active-build --current <app|ota> [threads]
active-build --help
```

Use `active-build -c app` or `active-build -c ota` only when `build/.config` already exists in the target workspace.

## Failure Reporting

When a command fails, keep the report concrete:

```text
Command: active-build -c ota 12
Cwd: /path/to/workspace
Exit code: 2
Key output:
...
```

If the failure happens after interactive confirmation or during `make`, preserve the visible command printed by the CLI after `>>> 执行:` so the user can retry manually.
