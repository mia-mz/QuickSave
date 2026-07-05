# Quicksave

Game-style save points for your work. Freeze your working state before an
interruption — browser tabs, terminal directories, VS Code workspaces, and
running AI coding sessions (Claude Code / Codex) — together with a short note
on what you were about to do. When you come back, load the save point and
only what is missing gets reopened.

Everything is stored locally as small JSON files. Nothing is uploaded.

## Why

Research by Gloria Mark (UC Irvine) found that interrupted work is resumed
after roughly 23 minutes on average, usually via several intervening tasks.
The expensive part of an interruption is not reopening windows — it is
reconstructing what you were thinking. A save point stores both.

## Install

```sh
git clone https://github.com/mia-mz/quicksave.git ~/Projects/quicksave
echo "alias qs='python3 ~/Projects/quicksave/quicksave.py'" >> ~/.zshrc
```

Python 3.9+, standard library only.

## Usage

```
qs save -m "title" [-n "notes"] [-p project]   create a save point
qs list                                        list save points
qs load [index]                                restore one
qs ui                                          web panel at 127.0.0.1:7799
```

On macOS, bind `qs` to a global hotkey with the Shortcuts app
(Run Shell Script → `python3 ~/Projects/quicksave/quicksave.py`).

## What restore does

- **Diff restore** — only tabs and terminal directories that are gone get
  reopened; anything still open is left untouched.
- **AI sessions** — `claude` sessions resume with their exact session id, in
  the host they were captured in: a session that lived in the VS Code
  integrated terminal reopens there (the workspace is opened first, and keys
  are only sent once VS Code owns the keyboard); a Terminal session goes back
  to a terminal window. `codex` reopens its session picker.
- **VS Code** — workspaces reopen via the `code` CLI; VS Code's own hot exit
  brings back editors and unsaved changes.
- A report at the end states exactly what was reopened, skipped, or failed.

## Platform support

| Capability                | macOS | Linux | Windows |
|---------------------------|:-----:|:-----:|:-------:|
| Browser tabs (capture)    | ✅    | —     | —       |
| Browser tabs (reopen)     | ✅    | ✅    | ✅      |
| Terminal directories      | ✅    | ✅    | —       |
| VS Code workspaces        | ✅    | ✅    | ✅      |
| Claude Code / Codex resume| ✅    | ✅    | —       |
| Web panel                 | ✅    | ✅    | ✅      |

Linux and Windows support is best-effort; capture backends beyond macOS are
welcome as contributions.

## Permissions (macOS, one-time)

- **Automation** — allow your terminal to control Chrome / Safari / Terminal
  / System Events (prompted on first use).
- **Accessibility** — required only for typing the resume command into the
  VS Code integrated terminal; without it, sessions fall back to Terminal.

## License

MIT
