# Quicksave

**Save a work session and restore it in seconds.**

Quicksave records the state of a work session and brings it back later. One save point captures the open browser tabs, the working directory of each terminal, the open VS Code workspaces, and any running Claude Code or Codex sessions, together with a short note on the task in progress. A later load rebuilds that state in seconds. Everything is stored as small local files.

## Why it helps

### Makes it safe to close things

Tabs and workspaces pile up because closing them means losing the context that took time to assemble. A save point records the full state first, so the whole session can be closed and brought back with one load.

### Frees memory

Background apps hold memory and can push a machine into the slow, force quit zone. A save point turns a running session into a few kilobytes on disk, so the apps can be quit and the memory handed back to the system. Loading the save point brings the session back.

### Removes the manual rebuild

Restarting a session by hand means reopening tabs one at a time, moving each terminal back to its folder, and opening every workspace again. Quicksave performs the whole rebuild in one step and leaves anything already open in place.

### Bridges the end of the day

A save point taken before stopping restores the next morning with tabs, terminals, and workspaces in place, including the unsaved editor changes that VS Code hot exit preserves.

## Built to be trusted

### Everything stays local

Save points are plain files on the same machine. No upload, no account, no server. Suitable for sensitive material such as health data.

### Restores are transparent

Each load ends with a plain report of how many tabs reopened, how many were already open, how many directories were skipped, and where each AI session resumed.

## What a save point captures

- **Browser tabs** across every window, so the research context returns intact
- **Terminal directories** for each open shell
- **VS Code workspaces**, including open files and unsaved edits through VS Code hot exit
- **Claude Code and Codex sessions**, resumed by their exact session id in the same place they were running
- **The intent**, one line plus optional notes on the next step

## Smart restore

Quicksave restores only the difference, so it stays out of the way.

- Tabs that are still open stay open. Only missing tabs reopen.
- Terminal directories already in use are left alone.
- AI sessions resume in the host they came from. A session from the VS Code integrated terminal reopens there, and a session from a terminal window returns to a terminal window.

## Install

```sh
git clone https://github.com/mia-mz/QuickSave.git ~/Projects/quicksave
echo "alias qs='python3 ~/Projects/quicksave/quicksave.py'" >> ~/.zshrc
```

Requires Python 3.9 or newer. Standard library only, so there is nothing else to install.

## Usage

```
qs save -m "title" [-n "notes"] [-p project]   create a save point
qs list                                        list save points
qs load [index]                                restore one
qs ui                                           web panel at 127.0.0.1:7799
qs doctor                                       check macOS permissions
```

On macOS, `qs` can be bound to a global hotkey with the Shortcuts app. Add a "Run Shell Script" action containing `python3 ~/Projects/quicksave/quicksave.py` and assign it a keyboard shortcut.

## Platform support

| Capability                 | macOS | Linux | Windows |
|----------------------------|:-----:|:-----:|:-------:|
| Firefox tabs               | Yes   | Yes   | Yes     |
| Chrome and Safari tabs     | Yes   | No    | No      |
| Terminal directories       | Yes   | Yes   | Yes     |
| VS Code workspaces         | Yes   | Yes   | Yes     |
| Claude Code / Codex resume | Yes   | Yes   | Yes     |
| Web panel                  | Yes   | Yes   | Yes     |

Firefox tabs are read from its session file on every platform. Chrome and Safari tab capture rely on AppleScript and stay macOS only. Terminal directories on Windows come from the Win32 process API, and AI sessions resume in a terminal window. On macOS an AI session that lived in the VS Code integrated terminal resumes there. The Windows backends read process state through PowerShell and the Win32 API and are ready for validation on a real Windows machine.

## Permissions on macOS

Two one time grants unlock the full experience.

- **Automation** lets the terminal control Chrome, Safari, Terminal, and System Events. macOS asks on first use.
- **Accessibility** lets Quicksave type a resume command into the VS Code integrated terminal. Run `qs doctor` to check the status and see which app to authorize. Without it, AI sessions still open inside VS Code with the command ready on the clipboard, so a single paste completes the resume.

## License

MIT
