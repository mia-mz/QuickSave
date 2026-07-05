# Quicksave

**Close everything. Get it all back in seconds.**

Quicksave gives your work a save point. Press it before you step away and it freezes your tabs, terminals, VS Code workspaces, and running AI coding sessions. Load it later and your workspace rebuilds itself in seconds. Everything stays on your machine as small local files.

## The problem

You keep forty tabs open because closing them feels like losing them. Your workspaces pile up for the same reason. Your Mac warns you about memory and you force quit things you were still using. At the end of the day you leave it all running, since tomorrow you will not remember where you were. This is one problem wearing four faces. There has never been a safe way to put your work down and pick it back up.

## What Quicksave does for your day

### Close without losing anything

Forty tabs and a stack of workspaces stay open only because you are afraid of losing them. A save point captures the whole state, so you can close all of it and trust that one load brings it back.

### Free your memory

Save, then quit. Your working state becomes a few kilobytes on disk and your memory returns to the system right away. The next time you need the work, load the save point and the difference comes back. You trade expensive RAM for a file smaller than a photo.

### Skip the manual rebuild

Reopening tabs one by one, typing cd into every folder, reopening each workspace by hand is slow and dull work. Quicksave does the entire rebuild for you and leaves anything already open exactly as it is.

### Walk away and come back tomorrow

Save at the end of the day. Load it the next morning and your tabs, terminals, and workspaces return, including the unsaved edits that VS Code hot exit keeps for you.

## Built to be trusted

### Your work stays on your machine

Everything is stored as small local files. No upload, no account, no server. Safe for sensitive material such as health data.

### You see exactly what happened

Every load ends with a plain report of what reopened, how many tabs were already open, how many directories were skipped, and where each AI session landed. You always know what Quicksave did on your behalf.

## What a save point captures

- **Browser tabs** across every window, so your research context returns intact
- **Terminal directories** for each shell you had open
- **VS Code workspaces**, including open files and unsaved edits through VS Code hot exit
- **Claude Code and Codex sessions**, resumed by their exact session id in the same place they were running
- **Your intent**, one line plus optional notes on what you were about to do next

## Smart restore

Quicksave restores the difference, so it stays out of your way.

- Only tabs that are gone get reopened. Anything still open stays as it is.
- Terminal directories already in use are left alone.
- AI sessions return to the host they came from. A session that lived in the VS Code integrated terminal reopens there, and a Terminal session goes back to a terminal window.

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

On macOS you can bind `qs` to a global hotkey with the Shortcuts app. Add a "Run Shell Script" action containing `python3 ~/Projects/quicksave/quicksave.py` and assign it a keyboard shortcut.

## Platform support

| Capability                 | macOS | Linux    | Windows  |
|----------------------------|:-----:|:--------:|:--------:|
| Browser tabs (capture)     | Yes   | Planned  | Planned  |
| Browser tabs (reopen)      | Yes   | Yes      | Yes      |
| Terminal directories       | Yes   | Yes      | Planned  |
| VS Code workspaces         | Yes   | Yes      | Yes      |
| Claude Code / Codex resume | Yes   | Yes      | Planned  |
| Web panel                  | Yes   | Yes      | Yes      |

macOS has full support today. Linux and Windows cover the core and are open for contributions on the remaining backends.

## Permissions on macOS

Two one time grants unlock the full experience.

- **Automation** lets your terminal control Chrome, Safari, Terminal, and System Events. macOS asks the first time you use it.
- **Accessibility** lets Quicksave type a resume command into the VS Code integrated terminal. Run `qs doctor` to see whether it is on and which app to authorize. Until you grant it, AI sessions still open inside VS Code with the command ready on your clipboard, so one paste finishes the job.

## License

MIT
