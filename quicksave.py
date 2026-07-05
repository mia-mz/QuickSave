#!/usr/bin/env python3
"""Quicksave — game-style save points for your work
Usage:
  qs save [-m "title"] [-n "notes"] [-p project]   create a save point
  qs list                                          list save points
  qs load [index]                                  restore one
  qs ui                                            open the web panel
All data stays local in ~/Library/Application Support/Quicksave (macOS)
or ~/.quicksave (Linux/Windows). Nothing is uploaded.

Platform support: macOS full; Linux best-effort (no browser capture);
Windows minimal (VS Code workspaces + AI sessions).
"""
import json, os, re, subprocess, sys, datetime, time, shutil, webbrowser
from urllib.parse import unquote

IS_MAC = sys.platform == 'darwin'
IS_LIN = sys.platform.startswith('linux')
IS_WIN = sys.platform == 'win32'

if IS_MAC:
    DATA_DIR = os.path.expanduser('~/Library/Application Support/Quicksave')
else:
    DATA_DIR = os.path.expanduser('~/.quicksave')
VAULT = os.path.join(DATA_DIR, 'saves')
PROJECTS_FILE = os.path.join(DATA_DIR, 'projects.json')

# ---------------- platform helpers ----------------

def osa(script, timeout=20):
    """Run AppleScript (macOS only), return stdout or None."""
    if not IS_MAC:
        return None
    try:
        r = subprocess.run(['osascript', '-e', script],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout.rstrip('\n') if r.returncode == 0 else None
    except Exception:
        return None

def app_running(name):
    out = osa(f'tell application "System Events" to (name of processes) contains "{name}"')
    return out == 'true'

def esc(s):
    return s.replace('\\', '\\\\').replace('"', '\\"')

def shq(path):
    return "'" + path.replace("'", "'\\''") + "'"

def notify(title, body):
    if IS_MAC:
        osa(f'display notification "{esc(body[:60])}" with title "{esc(title)}"')
    elif IS_LIN and shutil.which('notify-send'):
        subprocess.run(['notify-send', title, body[:60]], timeout=5)

def load_projects():
    try:
        return json.load(open(PROJECTS_FILE)).get('projects', [])
    except Exception:
        return []

def add_project(name):
    name = (name or '').strip()[:24]
    if not name:
        return load_projects()
    lst = load_projects()
    if name not in lst:
        lst.append(name)
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump({'projects': lst}, open(PROJECTS_FILE, 'w'), ensure_ascii=False)
    return lst

def _proc_table():
    """pid -> (ppid, command path)."""
    if IS_WIN:
        return {}
    try:
        out = subprocess.run(['ps', '-ax', '-o', 'pid=,ppid=,comm='],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return {}
    t = {}
    for line in out.splitlines():
        b = line.split(None, 2)
        if len(b) == 3:
            t[b[0]] = (b[1], b[2])
    return t

def _ancestors(pid, table, limit=20):
    out = []
    for _ in range(limit):
        if pid not in table:
            break
        ppid, comm = table[pid]
        out.append((pid, comm))
        pid = ppid
    return out

def _cwd_of(pid):
    if IS_LIN:
        try:
            return os.readlink(f'/proc/{pid}/cwd')
        except Exception:
            return None
    try:
        l = subprocess.run(['lsof', '-a', '-p', pid, '-d', 'cwd', '-Fn'],
                           capture_output=True, text=True, timeout=10).stdout
        for ln in l.splitlines():
            if ln.startswith('n'):
                return ln[1:]
    except Exception:
        pass
    return None

def _vscode_storage():
    if IS_MAC:
        return os.path.expanduser('~/Library/Application Support/Code/User/globalStorage/storage.json')
    if IS_WIN:
        return os.path.join(os.environ.get('APPDATA', ''), 'Code', 'User', 'globalStorage', 'storage.json')
    return os.path.expanduser('~/.config/Code/User/globalStorage/storage.json')

def _code_cli():
    c = shutil.which('code')
    if c:
        return c
    if IS_MAC and os.path.exists('/Applications/Visual Studio Code.app'):
        return '/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code'
    if IS_WIN:
        p = os.path.expandvars(r'%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd')
        if os.path.exists(p):
            return p
    return None

def _spawn_terminal(cwd, cmd=None):
    """Open a terminal window at cwd, optionally running cmd. Best effort."""
    if IS_MAC:
        full = f'cd {shq(cwd)}' + (f' && {cmd}' if cmd else '')
        osa(f'tell application "Terminal"\nactivate\ndo script "{esc(full)}"\nend tell', timeout=30)
        return True
    if IS_LIN:
        inner = f'cd {shq(cwd)}' + (f' && {cmd}' if cmd else '') + '; exec bash'
        for t in (['gnome-terminal', '--'], ['konsole', '-e'],
                  ['xfce4-terminal', '-e'], ['x-terminal-emulator', '-e']):
            if shutil.which(t[0]):
                try:
                    subprocess.Popen(t + ['bash', '-lc', inner])
                    return True
                except Exception:
                    continue
        return False
    if IS_WIN:
        try:
            inner = f'cd /d "{cwd}"' + (f' && {cmd}' if cmd else '')
            subprocess.Popen(['cmd', '/c', 'start', 'cmd', '/K', inner])
            return True
        except Exception:
            return False
    return False

# ---------------- capture ----------------

def capture_front():
    if not IS_MAC:
        return None
    out = osa('''tell application "System Events"
  set p to first application process whose frontmost is true
  set nm to name of p
  set wt to ""
  try
    set wt to name of front window of p
  end try
  return nm & linefeed & wt
end tell''')
    if not out:
        return None
    parts = out.split('\n')
    return {'app': parts[0], 'window': parts[1] if len(parts) > 1 else ''}

def capture_browser(app):
    """macOS only: list of windows, each a list of {url,title}."""
    if not IS_MAC or not app_running(app):
        return []
    if app == 'Google Chrome':
        script = '''tell application "Google Chrome"
  set o to ""
  repeat with w in windows
    set o to o & "===WINDOW===" & linefeed
    repeat with t in tabs of w
      set o to o & (URL of t) & linefeed & (title of t) & linefeed
    end repeat
  end repeat
  return o
end tell'''
    else:
        script = '''tell application "Safari"
  set o to ""
  repeat with w in windows
    set o to o & "===WINDOW===" & linefeed
    repeat with t in tabs of w
      set o to o & (URL of t) & linefeed & (name of t) & linefeed
    end repeat
  end repeat
  return o
end tell'''
    out = osa(script, timeout=30)
    if not out:
        return []
    wins, cur = [], None
    lines = out.split('\n')
    i = 0
    while i < len(lines):
        if lines[i] == '===WINDOW===':
            cur = []; wins.append(cur); i += 1
        elif cur is not None:
            url = lines[i]
            title = lines[i+1] if i + 1 < len(lines) else ''
            if url and url != '===WINDOW===':
                cur.append({'url': url, 'title': title})
            i += 2
        else:
            i += 1
    return [w for w in wins if w]

def _agent_pids(table):
    """pids of running claude/codex CLI processes (with a tty)."""
    if IS_WIN:
        return {}
    try:
        out = subprocess.run(['ps', '-ax', '-o', 'pid=,tty=,command='],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return {}
    found = {}
    for line in out.splitlines():
        bits = line.split(None, 2)
        if len(bits) != 3:
            continue
        pid, tty, args = bits
        if not (tty.startswith('ttys') or tty.startswith('pts')):
            continue
        for t in args.split()[:2]:
            base = t.rsplit('/', 1)[-1]
            if base in ('claude', 'codex'):
                found[pid] = base
                break
    return found

def _shell_cwds():
    """cwds of interactive shells, excluding scratch dirs and shells that are
    descendants of an AI agent."""
    if IS_WIN:
        return []
    table = _proc_table()
    agents = set(_agent_pids(table))
    try:
        out = subprocess.run(['ps', '-ax', '-o', 'pid=,tty=,comm='],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        bits = line.split(None, 2)
        if len(bits) != 3:
            continue
        pid, tty, comm = bits
        if not (tty.startswith('ttys') or tty.startswith('pts')):
            continue
        base = comm.rsplit('/', 1)[-1].lstrip('-')
        if base not in ('zsh', 'bash', 'fish', 'sh'):
            continue
        if any(p in agents for p, _ in _ancestors(pid, table)):
            continue
        pids.append(pid)
    if not pids:
        return []
    cwds, seen = [], set()
    if IS_LIN:
        raw = [_cwd_of(p) for p in pids]
    else:
        raw = []
        try:
            l = subprocess.run(['lsof', '-a', '-p', ','.join(pids), '-d', 'cwd', '-Fn'],
                               capture_output=True, text=True, timeout=15).stdout
            raw = [ln[1:] for ln in l.splitlines() if ln.startswith('n')]
        except Exception:
            pass
    for c in raw:
        if not c or c == '/' or c in seen:
            continue
        if c.startswith(('/tmp', '/private/tmp', '/private/var')):
            continue
        seen.add(c); cwds.append(c)
    return cwds

def capture_terminals():
    cwds = _shell_cwds()
    if not cwds:
        return None
    app = 'iTerm2' if (IS_MAC and app_running('iTerm2')) else 'Terminal'
    return {'app': app, 'cwds': cwds}

def capture_vscode():
    try:
        st = json.load(open(_vscode_storage()))
        wins = st.get('windowsState', {}).get('openedWindows', [])
        last = st.get('windowsState', {}).get('lastActiveWindow')
        if last:
            wins = [last] + wins
        out, seen = [], set()
        for w in wins:
            uri = (w or {}).get('folder', '')
            if uri.startswith('file://'):
                path = unquote(uri[7:])
                if IS_WIN and re.match(r'^/[A-Za-z]:', path):
                    path = path[1:]
                if path and path not in seen and os.path.exists(path):
                    seen.add(path); out.append(path)
        return out
    except Exception:
        return []

def _host_of(pid, table):
    for _pid, comm in _ancestors(pid, table):
        name = comm.rsplit('/', 1)[-1]
        if 'Code Helper' in comm or name in ('Electron', 'Code', 'code'):
            return 'vscode'
        if name in ('Terminal', 'iTerm2', 'iTerm', 'WarpTerminal', 'ghostty',
                    'kitty', 'alacritty', 'Hyper', 'gnome-terminal-server',
                    'konsole', 'xterm'):
            return 'terminal'
    return 'terminal'

def capture_agents():
    """Running Claude Code / Codex sessions: tool, cwd, host app, resume id."""
    table = _proc_table()
    found, seen = [], set()
    for pid, tool in _agent_pids(table).items():
        cwd = _cwd_of(pid)
        if not cwd or (tool, cwd) in seen:
            continue
        seen.add((tool, cwd))
        a = {'tool': tool, 'cwd': cwd, 'host': _host_of(pid, table)}
        if tool == 'claude':
            sid = _claude_session_for(cwd)
            if sid:
                a['session'] = sid
        found.append(a)
    return found

def _claude_session_for(cwd):
    base = os.path.expanduser('~/.claude/projects/')
    for enc in (cwd.replace('/', '-'), re.sub(r'[/.]', '-', cwd)):
        d = base + enc
        try:
            files = [f for f in os.listdir(d) if f.endswith('.jsonl')]
            if files:
                newest = max(files, key=lambda x: os.path.getmtime(os.path.join(d, x)))
                return newest[:-6]
        except Exception:
            continue
    return None

# ---------------- save ----------------

def ask_intent():
    if IS_MAC:
        out = osa('''display dialog "此刻你正要干嘛?一句话。" default answer "" with title "Quicksave" buttons {"取消", "存档"} default button "存档"''', timeout=120)
        if not out or 'text returned:' not in out:
            return None
        return out.split('text returned:')[-1].strip()
    try:
        return input('标题(此刻你正要干嘛): ').strip() or None
    except (EOFError, KeyboardInterrupt):
        return None

def do_save(intent=None, project='', note=''):
    if intent is None:
        intent = ask_intent()
        if intent is None:
            print('已取消。'); return
    ts = datetime.datetime.now()
    slug = ts.strftime('%Y%m%d-%H%M%S')
    d = os.path.join(VAULT, slug)
    os.makedirs(d, exist_ok=True)
    state = {
        'ts': ts.isoformat(timespec='seconds'),
        'intent': intent,
        'note': note or '',
        'project': project or '',
        'front': capture_front(),
        'chrome': capture_browser('Google Chrome'),
        'safari': capture_browser('Safari'),
        'terminals': capture_terminals(),
        'vscode': capture_vscode(),
        'agents': capture_agents(),
    }
    json.dump(state, open(os.path.join(d, 'state.json'), 'w'),
              ensure_ascii=False, indent=1)
    ntabs = sum(len(w) for w in state['chrome']) + sum(len(w) for w in state['safari'])
    nterm = len(state['terminals']['cwds']) if state['terminals'] else 0
    nag = len(state['agents'])
    print(f'✅ 存档完成 {slug}' + (f'  [{project}]' if project else ''))
    print(f'   {intent}')
    print(f'   浏览器 {ntabs} 标签 · 终端 {nterm} 目录 · VS Code {len(state["vscode"])} 工作区 · AI 会话 {nag}')
    if not IS_MAC:
        print('   (本平台不支持浏览器标签采集)')
    notify('Quicksave', intent[:40])
    return slug

# ---------------- restore ----------------

def restore_browser(app, wins):
    """Reopen only the tabs that are gone. Returns (opened, skipped)."""
    if not wins:
        return 0, 0
    current = set()
    for w in capture_browser(app):
        for t in w:
            current.add(t.get('url'))
    opened = skipped = 0
    for w in wins:
        urls, have = [], 0
        for t in w:
            u = t.get('url', '')
            if not u.startswith(('http', 'file')):
                continue
            if u in current:
                have += 1
            else:
                urls.append(u)
        skipped += have
        if not urls:
            continue
        if IS_MAC and app == 'Google Chrome':
            lines = ['set w to make new window',
                     f'set URL of active tab of w to "{esc(urls[0])}"']
            for u in urls[1:]:
                lines.append(f'tell w to make new tab with properties {{URL:"{esc(u)}"}}')
            osa('tell application "Google Chrome"\nactivate\n' + '\n'.join(lines) + '\nend tell', timeout=60)
        elif IS_MAC and app == 'Safari':
            lines = ['set d to make new document with properties {URL:"%s"}' % esc(urls[0])]
            for u in urls[1:]:
                lines.append(f'tell front window to set t to make new tab with properties {{URL:"{esc(u)}"}}')
            osa('tell application "Safari"\nactivate\n' + '\n'.join(lines) + '\nend tell', timeout=60)
        else:
            for u in urls:
                webbrowser.open(u)
        opened += len(urls)
    return opened, skipped

def restore_terminals(term, exclude=None):
    """Reopen dirs no live shell is in. Returns (opened_dirs, skipped)."""
    if not term or not term.get('cwds'):
        return [], 0
    live = set(_shell_cwds()) | (exclude or set())
    want = [c for c in term['cwds'] if c not in live and os.path.isdir(c)]
    cwds, extra = want[:5], max(0, len(want) - 5)
    if not cwds:
        return [], len(term['cwds'])
    if IS_MAC and term['app'] == 'iTerm2':
        lines = ['set w to create window with default profile',
                 f'tell current session of w to write text "cd {shq(cwds[0])}"']
        for c in cwds[1:]:
            lines.append('tell w')
            lines.append('  set t to create tab with default profile')
            lines.append(f'  tell current session of t to write text "cd {shq(c)}"')
            lines.append('end tell')
        osa('tell application "iTerm2"\nactivate\n' + '\n'.join(lines) + '\nend tell', timeout=60)
    else:
        for c in cwds:
            _spawn_terminal(c)
    return cwds, len(term['cwds']) - len(cwds) + extra

def restore_vscode(folders):
    code = _code_cli()
    for f in folders[:4]:
        if code:
            subprocess.run([code, f], timeout=15)
        elif IS_MAC:
            subprocess.run(['open', '-a', 'Visual Studio Code', f])
    return len(folders[:4])

def _frontmost():
    return osa('tell application "System Events" to get name of first application process whose frontmost is true')

def _resume_in_vscode(folder, cmd):
    """macOS: open the workspace, wait until VS Code is frontmost, then spawn
    a fresh integrated terminal (^⇧`) and paste the resume command.
    Never types unless VS Code owns the keyboard."""
    if not IS_MAC:
        return False
    code = _code_cli()
    if code:
        subprocess.run([code, folder], timeout=15)
    else:
        subprocess.run(['open', '-a', 'Visual Studio Code', folder])
    for _ in range(14):
        if _frontmost() == 'Code':
            break
        time.sleep(0.5)
    else:
        return False
    old_clip = subprocess.run(['pbpaste'], capture_output=True, text=True).stdout
    subprocess.run(['pbcopy'], input=cmd, text=True)
    ok = osa('''tell application "System Events"
  if name of first application process whose frontmost is true is not "Code" then return "lost"
  tell process "Code"
    keystroke "`" using {control down, shift down}
    delay 1.0
    if name of first application process whose frontmost is true is not "Code" then return "lost"
    keystroke "v" using command down
    delay 0.3
    key code 36
  end tell
end tell
return "ok"''', timeout=30)
    subprocess.run(['pbcopy'], input=old_clip, text=True)
    return ok == 'ok'

def restore_agents(agents):
    """Resume AI sessions in the host they were captured in.
    Returns (claimed_cwds, detail list of (name, how))."""
    if not agents:
        return set(), []
    cur = {(a['tool'], a['cwd']) for a in capture_agents()}
    used, detail = set(), []
    for a in agents:
        cwd = a.get('cwd', '')
        name = f"{a['tool']} @ {cwd.rsplit('/', 1)[-1] or cwd}"
        if not os.path.isdir(cwd):
            detail.append((name, 'gone')); continue
        if (a['tool'], cwd) in cur:
            used.add(cwd); detail.append((name, 'already')); continue
        if a['tool'] == 'claude':
            cmd = 'claude --resume ' + a['session'] if a.get('session') else 'claude --continue'
        else:
            cmd = 'codex resume'
        used.add(cwd)
        if a.get('host') == 'vscode' and _resume_in_vscode(cwd, cmd):
            detail.append((name, 'vscode')); continue
        ok = _spawn_terminal(cwd, cmd)
        if a.get('host') == 'vscode':
            detail.append((name, 'terminal-fallback' if ok else 'failed'))
        else:
            detail.append((name, 'terminal' if ok else 'failed'))
    return used, detail

# ---------------- list / load ----------------

def all_saves():
    if not os.path.isdir(VAULT):
        return []
    out = []
    for slug in sorted(os.listdir(VAULT), reverse=True):
        p = os.path.join(VAULT, slug, 'state.json')
        if os.path.isfile(p):
            try:
                out.append((slug, json.load(open(p))))
            except Exception:
                pass
    return out

def label(slug, st):
    t = datetime.datetime.fromisoformat(st['ts']).strftime('%m-%d %H:%M')
    ntabs = sum(len(w) for w in st.get('chrome', [])) + sum(len(w) for w in st.get('safari', []))
    nterm = len(st['terminals']['cwds']) if st.get('terminals') else 0
    nag = len(st.get('agents') or [])
    intent = (st.get('intent') or '(无)')[:24]
    proj = ('[' + st['project'] + '] ') if st.get('project') else ''
    return f'{t} · {proj}{intent} · {ntabs}标签/{nterm}终端/{nag}AI'

def do_list():
    saves = all_saves()
    if not saves:
        print('还没有存档。用  qs save  建第一个。'); return
    for i, (slug, st) in enumerate(saves, 1):
        print(f'{i:3d}. {label(slug, st)}')

AGENT_WORDS_CN = {'vscode': '已在 VS Code 内置终端接上', 'terminal': '已在终端接上',
                  'terminal-fallback': '进不去 VS Code(缺辅助功能权限),改在终端接上',
                  'already': '本来就开着,没动', 'gone': '项目目录已不存在,跳过',
                  'failed': '没能自动打开,请手动恢复'}

def _pick_save(saves):
    items = [label(s, st) for s, st in saves[:20]]
    if IS_MAC:
        lst = ', '.join('"' + esc(x) + '"' for x in items)
        out = osa(f'choose from list {{{lst}}} with title "Quicksave" with prompt "回到哪个瞬间?" OK button name "读档" cancel button name "算了"', timeout=120)
        if not out or out == 'false':
            return None
        try:
            return items.index(out) + 1
        except ValueError:
            return None
    for i, x in enumerate(items, 1):
        print(f'{i:3d}. {x}')
    try:
        n = input('读哪个档(编号): ').strip()
        return int(n) if n.isdigit() and 1 <= int(n) <= len(items) else None
    except (EOFError, KeyboardInterrupt):
        return None

def do_load(idx=None, interactive=True):
    saves = all_saves()
    if not saves:
        print('没有存档。'); return None
    if idx is None:
        idx = _pick_save(saves)
        if idx is None:
            print('已取消。'); return None
    slug, st = saves[idx - 1]
    print(f'读档: {label(slug, st)}')
    ncode = restore_vscode(st.get('vscode', []))
    used, agent_detail = restore_agents(st.get('agents', []))
    c_open, c_skip = restore_browser('Google Chrome', st.get('chrome', []))
    s_open, s_skip = restore_browser('Safari', st.get('safari', []))
    t_open, t_skip = restore_terminals(st.get('terminals'), exclude=used)
    summary = {
        'intent': st.get('intent') or '',
        'note': st.get('note') or '',
        'tabs_opened': c_open + s_open, 'tabs_already': c_skip + s_skip,
        'terms_opened': t_open, 'terms_skipped': t_skip,
        'code': ncode,
        'agents': [{'name': n, 'how': h} for n, h in agent_detail],
    }
    print(f"   补开标签 {summary['tabs_opened']}(已开着 {summary['tabs_already']} 个没动)"
          f" · 终端 {len(t_open)} · VS Code {ncode}")
    for a in summary['agents']:
        print(f"   {a['name']}: {AGENT_WORDS_CN.get(a['how'], a['how'])}")
    if interactive and summary['intent'] and IS_MAC:
        osa(f'display dialog "{esc(summary["intent"])}" with title "你当时说" buttons {{"好"}} default button "好"', timeout=120)
    print('✅ 恢复完成。')
    return summary

# ---------------- web ui ----------------

UI_HTML = r'''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quicksave</title>
<style>
:root{
  --bg:#14161d; --card:#1b1e2a; --card2:#20242f; --line:rgba(214,222,255,.08);
  --ink:#e8ebf5; --dim:#8b91a8; --gold:#f0b849; --gold-dim:rgba(240,184,73,.14);
  --red:#ff8d7a;
  --mono:ui-monospace,"SF Mono",Menlo,monospace;
  --serif:Georgia,"Times New Roman",serif;
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,#171a23,#12141b 60%,#0e1015);color:var(--ink);
  font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}
.wrap{max-width:760px;margin:0 auto;padding:44px 24px 80px}
header{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px;gap:16px;flex-wrap:wrap}
.brand .word{font:600 13px var(--mono);letter-spacing:.42em;color:var(--gold);text-transform:uppercase}
.brand h1{margin:2px 0 0;font-size:26px;font-weight:700;letter-spacing:.01em}
.newbtn{
  appearance:none;border:1px solid rgba(240,184,73,.55);background:var(--gold-dim);color:#ffd98f;
  font:600 14px/1 inherit;padding:12px 22px;border-radius:12px;cursor:pointer;letter-spacing:.03em;
  transition:background .15s, transform .1s;
}
.newbtn:hover{background:rgba(240,184,73,.24)} .newbtn:active{transform:scale(.98)}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 20px}
.tab{
  appearance:none;border:1px solid var(--line);background:var(--card2);color:var(--dim);
  font:12.5px/1 inherit;padding:8px 15px;border-radius:999px;cursor:pointer;
  transition:border-color .15s,color .15s;
}
.tab:hover{border-color:rgba(214,222,255,.25)}
.tab.on{border-color:rgba(240,184,73,.55);color:#ffd98f;background:var(--gold-dim)}
.tab.add{border-style:dashed}
.slots{display:flex;flex-direction:column;gap:13px}
.slot{
  border:1px solid var(--line);border-radius:16px;background:var(--card);
  padding:16px 20px 13px;transition:border-color .15s, transform .12s;
}
.slot:hover{border-color:rgba(240,184,73,.4);transform:translateY(-1px)}
.slot.hi{border-color:rgba(240,184,73,.65);box-shadow:0 0 0 1px rgba(240,184,73,.3)}
.when{display:flex;gap:10px;align-items:baseline;font:12px var(--mono);color:var(--dim);letter-spacing:.04em}
.when .slotno{
  font-weight:600;font-size:10px;letter-spacing:.18em;color:var(--gold);
  border:1px solid rgba(240,184,73,.35);background:var(--gold-dim);
  padding:2px 8px;border-radius:6px;
}
.when .rel{color:#c9cfdf}
.intent{
  font-family:var(--serif);font-size:19px;line-height:1.5;font-weight:600;margin:7px 0 3px;
  overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
}
.intent.none{color:var(--dim);font-weight:400;font-style:italic}
.notep{
  color:var(--dim);font-size:13px;margin:0 0 8px;
  overflow:hidden;display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;
}
.chips{display:flex;gap:7px;flex-wrap:wrap}
.chip{font-size:11.5px;color:var(--dim);border:1px solid var(--line);background:var(--card2);
  padding:2.5px 9px;border-radius:999px;white-space:nowrap}
.chip b{color:#c9cfdf;font-weight:600}
.acts{display:flex;gap:10px;margin-top:11px;align-items:center}
.load{
  appearance:none;border:0;background:var(--gold);color:#241a05;font:700 13px/1 inherit;
  padding:9px 20px;border-radius:9px;cursor:pointer;letter-spacing:.04em;transition:filter .15s;
}
.load:hover{filter:brightness(1.08)}
.load:disabled{opacity:.5;cursor:default}
.ghost{appearance:none;border:0;background:none;color:var(--dim);font:12px inherit;cursor:pointer;
  padding:8px 10px;border-radius:8px;opacity:0;transition:opacity .15s}
.slot:hover .ghost{opacity:1}
.ghost:hover{color:var(--red);background:rgba(255,120,100,.08)}
.empty{border:1.5px dashed var(--line);border-radius:16px;padding:60px 20px;text-align:center;color:var(--dim)}
.empty b{color:var(--ink)}
kbd{font:11px var(--mono);border:1px solid var(--line);border-bottom-width:2px;border-radius:5px;
  padding:1px 6px;background:var(--card2);color:var(--dim)}
.foot{margin-top:36px;text-align:center;color:rgba(139,145,168,.7);font-size:12px}
.veil{position:fixed;inset:0;background:rgba(8,9,13,.66);backdrop-filter:blur(4px);
  display:flex;align-items:flex-start;justify-content:center;padding-top:14vh;z-index:20}
.modal{width:min(92vw,540px);background:var(--card);border:1px solid rgba(240,184,73,.35);
  border-radius:16px;padding:24px;box-shadow:0 30px 80px rgba(0,0,0,.6)}
.modal h2{margin:0 0 4px;font-size:17px}
.modal p{margin:0 0 14px;color:var(--dim);font-size:13px}
.modal .quote{font-family:var(--serif);font-size:19px;font-weight:600;margin:8px 0 6px}
.modal .notefull{color:var(--dim);font-size:13.5px;white-space:pre-wrap;margin:0 0 12px}
.modal ul{margin:0 0 6px;padding-left:18px;color:var(--dim);font-size:13.5px}
.modal ul b{color:var(--ink)}
.modal input,.modal textarea{
  width:100%;font:15px/1.6 inherit;color:var(--ink);background:var(--card2);
  border:1px solid var(--line);border-radius:10px;padding:11px 14px;outline:none;resize:vertical;
}
.modal textarea{margin-top:10px;min-height:76px}
.modal input:focus,.modal textarea:focus{border-color:rgba(240,184,73,.5)}
.mrow{display:flex;justify-content:flex-end;gap:10px;margin-top:14px}
.mrow .ghost{opacity:1}
.toast{
  position:fixed;left:50%;bottom:34px;transform:translateX(-50%);z-index:30;
  background:#0d0f15;border:1px solid rgba(240,184,73,.45);color:#ffd98f;
  padding:11px 22px;border-radius:999px;font-size:13.5px;box-shadow:0 14px 40px rgba(0,0,0,.5);
  opacity:0;transition:opacity .25s;pointer-events:none;
}
.toast.on{opacity:1}
</style></head><body>
<div class="wrap">
  <header>
    <div class="brand">
      <div class="word">Quicksave</div>
      <h1>Save points</h1>
    </div>
    <button class="newbtn" id="newBtn">New save point</button>
  </header>
  <div class="tabs" id="tabs"></div>
  <div class="slots" id="slots"></div>
  <div class="foot">↑/↓ select · <kbd>Enter</kbd> to load · <span style="font-family:var(--mono)">qs save</span> from any terminal</div>
</div>
<div class="toast" id="toast"></div>
<script>
const TOKEN='__TOKEN__';
const $=s=>document.querySelector(s);
const api=(url,opt)=>fetch(url,Object.assign({headers:{'X-QS-Token':TOKEN,'Content-Type':'application/json'}},opt||{}));
const AGENT_TEXT={
  vscode:'resumed in the VS Code terminal',
  terminal:'resumed in a terminal window',
  'terminal-fallback':'VS Code input unavailable (accessibility permission) — resumed in a terminal',
  already:'already running, left untouched',
  gone:'project folder no longer exists, skipped',
  failed:'could not reopen automatically'
};
let saves=[], hi=-1, projects=[], cur=localStorage.getItem('qs.proj')||'__all__';
async function loadProjects(){
  projects=await (await api('/api/projects')).json();
  if(cur!=='__all__' && !projects.includes(cur)){cur='__all__';}
  renderTabs();
}
function renderTabs(){
  const box=$('#tabs'); box.textContent='';
  const mk=(label,val,cls)=>{
    const b=document.createElement('button');
    b.className='tab'+(cls?' '+cls:'')+(cur===val?' on':'');
    b.textContent=label;
    b.onclick=()=>{
      if(val==='__new__'){
        const name=prompt('Project name');
        if(!name||!name.trim()) return;
        api('/api/projects',{method:'POST',body:JSON.stringify({name:name.trim()})}).then(async r=>{
          projects=await r.json(); cur=name.trim();
          localStorage.setItem('qs.proj',cur); renderTabs(); refresh();
        });
        return;
      }
      cur=val; localStorage.setItem('qs.proj',cur); renderTabs(); refresh();
    };
    box.appendChild(b);
  };
  mk('All','__all__');
  projects.forEach(p=>mk(p,p));
  mk('+ New project','__new__','add');
}
function rel(iso){
  const d=(Date.now()-new Date(iso))/60000;
  if(d<1) return 'just now';
  if(d<60) return Math.floor(d)+'m ago';
  if(d<1440) return Math.floor(d/60)+'h ago';
  return Math.floor(d/1440)+'d ago';
}
function escH(s){const d=document.createElement('i');d.textContent=s;return d.innerHTML}
async function refresh(){
  const all=await (await api('/api/saves')).json();
  saves=cur==='__all__'?all:all.filter(s=>s.project===cur);
  const box=$('#slots'); box.textContent='';
  if(!saves.length){
    box.innerHTML='<div class="empty"><b>No save points yet.</b><br>Create one before you step away.</div>';
    return;
  }
  saves.forEach((s,i)=>{
    const el=document.createElement('div');
    el.className='slot'+(i===hi?' hi':'');
    const chips=[];
    if(s.project&&cur==='__all__') chips.push('<span class="chip"><b>'+escH(s.project)+'</b></span>');
    if(s.agents) chips.push('<span class="chip"><b>'+s.agents+'</b> AI session'+(s.agents>1?'s':'')+'</span>');
    if(s.tabs) chips.push('<span class="chip"><b>'+s.tabs+'</b> tabs</span>');
    if(s.terms) chips.push('<span class="chip"><b>'+s.terms+'</b> terminals</span>');
    if(s.code) chips.push('<span class="chip"><b>'+s.code+'</b> workspace'+(s.code>1?'s':'')+'</span>');
    if(s.front) chips.push('<span class="chip">in <b>'+escH(s.front)+'</b></span>');
    el.innerHTML=
      '<div class="when"><span class="slotno">SLOT '+String(saves.length-i).padStart(2,'0')+'</span>'+
      '<span class="rel">'+rel(s.ts)+'</span><span>'+s.ts.replace('T',' ')+'</span></div>'+
      '<div class="intent'+(s.intent?'':' none')+'">'+(s.intent?escH(s.intent):'Untitled')+'</div>'+
      (s.note?'<p class="notep">'+escH(s.note)+'</p>':'')+
      '<div class="chips">'+chips.join('')+'</div>'+
      '<div class="acts"><button class="load">Load</button><button class="ghost">Delete</button></div>';
    el.querySelector('.load').onclick=()=>doLoad(s,el);
    el.querySelector('.ghost').onclick=async e=>{
      e.stopPropagation();
      if(!confirm('Delete this save point?')) return;
      await api('/api/delete/'+s.slug,{method:'POST'});
      refresh();
    };
    box.appendChild(el);
  });
}
async function doLoad(s,el){
  const b=el.querySelector('.load'); b.disabled=true; b.textContent='Loading…';
  toast('Restoring…');
  let r=null;
  try{ r=await (await api('/api/load/'+s.slug,{method:'POST'})).json(); }catch(e){}
  b.disabled=false; b.textContent='Load';
  if(!r||!r.summary){ toast('Restore failed — check the terminal output'); return; }
  showResult(r.summary);
}
function showResult(sm){
  const v=document.createElement('div'); v.className='veil';
  let items='<li>Reopened <b>'+sm.tabs_opened+'</b> tabs — <b>'+sm.tabs_already+'</b> already open</li>'+
    '<li><b>'+sm.terms_opened.length+'</b> terminal directories'+(sm.terms_skipped?' ('+sm.terms_skipped+' already in use)':'')+'</li>'+
    '<li><b>'+sm.code+'</b> VS Code workspaces</li>';
  (sm.agents||[]).forEach(a=>{items+='<li>'+escH(a.name)+' — '+(AGENT_TEXT[a.how]||a.how)+'</li>';});
  v.innerHTML='<div class="modal"><h2>Restored</h2>'+
    (sm.intent?'<div class="quote">'+escH(sm.intent)+'</div>':'')+
    (sm.note?'<p class="notefull">'+escH(sm.note)+'</p>':'')+
    '<ul>'+items+'</ul>'+
    '<div class="mrow"><button class="load" id="ok">Done</button></div></div>';
  document.body.appendChild(v);
  v.querySelector('#ok').onclick=()=>v.remove();
  v.addEventListener('click',e=>{if(e.target===v)v.remove();});
}
let toastT=0;
function toast(m){
  const t=$('#toast'); t.textContent=m; t.classList.add('on');
  clearTimeout(toastT); toastT=setTimeout(()=>t.classList.remove('on'),3200);
}
$('#newBtn').onclick=()=>{
  const proj=cur==='__all__'?'':cur;
  const v=document.createElement('div'); v.className='veil';
  v.innerHTML='<div class="modal"><h2>New save point</h2>'+
    '<p>'+(proj?'Filed under '+escH(proj)+'.':'Unfiled — select a project tab first to file it.')+'</p>'+
    '<input id="mi" maxlength="80" placeholder="Title">'+
    '<textarea id="mn" maxlength="2000" placeholder="Notes (optional)"></textarea>'+
    '<div class="mrow"><button class="ghost" id="mc">Cancel</button><button class="load" id="mo">Save</button></div></div>';
  document.body.appendChild(v);
  const inp=v.querySelector('#mi'); inp.focus();
  const go=async()=>{
    const intent=inp.value.trim();
    if(!intent){inp.focus();return;}
    const note=v.querySelector('#mn').value.trim();
    v.remove(); toast('Saving…');
    await api('/api/save',{method:'POST',body:JSON.stringify({intent,note,project:proj})});
    toast('Saved'); refresh();
  };
  v.querySelector('#mo').onclick=go;
  inp.addEventListener('keydown',e=>{if(e.key==='Enter')go(); if(e.key==='Escape')v.remove();});
  v.querySelector('#mc').onclick=()=>v.remove();
  v.addEventListener('click',e=>{if(e.target===v)v.remove();});
};
document.addEventListener('keydown',e=>{
  if(document.querySelector('.veil')) return;
  if(e.key==='ArrowDown'){hi=Math.min(saves.length-1,hi+1);refresh();e.preventDefault();}
  else if(e.key==='ArrowUp'){hi=Math.max(0,hi-1);refresh();e.preventDefault();}
  else if(e.key==='Enter'&&hi>=0){const el=document.querySelectorAll('.slot')[hi];if(el)doLoad(saves[hi],el);}
});
loadProjects().then(refresh);
setInterval(refresh,60000);
</script></body></html>'''

def run_ui(port=7799, open_browser=True):
    import secrets
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    token = secrets.token_hex(16)
    page = UI_HTML.replace('__TOKEN__', token).encode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            return self.headers.get('X-QS-Token') == token

        def do_GET(self):
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            if not self.path.startswith('/api/'):
                self.send_error(404); return
            if not self._authed():
                self._json({'err': 'bad token'}, 403); return
            if self.path == '/api/saves':
                out = []
                for slug, st in all_saves():
                    out.append({
                        'slug': slug, 'ts': st['ts'], 'intent': st.get('intent') or '',
                        'note': st.get('note') or '',
                        'project': st.get('project') or '',
                        'tabs': sum(len(w) for w in st.get('chrome', [])) + sum(len(w) for w in st.get('safari', [])),
                        'terms': len(st['terminals']['cwds']) if st.get('terminals') else 0,
                        'code': len(st.get('vscode', [])),
                        'agents': len(st.get('agents') or []),
                        'front': (st.get('front') or {}).get('app', ''),
                    })
                self._json(out)
            elif self.path == '/api/projects':
                self._json(load_projects())
            else:
                self.send_error(404)

        def do_POST(self):
            if not self._authed():
                self._json({'err': 'bad token'}, 403); return
            m = re.fullmatch(r'/api/load/(\d{8}-\d{6})', self.path)
            if m:
                slug = m.group(1)
                for i, (s, _st) in enumerate(all_saves(), 1):
                    if s == slug:
                        sm = do_load(i, interactive=False)
                        self._json({'ok': True, 'summary': sm}); return
                self._json({'ok': False}, 404); return
            m = re.fullmatch(r'/api/delete/(\d{8}-\d{6})', self.path)
            if m:
                import shutil as _sh
                d = os.path.join(VAULT, m.group(1))
                if os.path.isdir(d):
                    _sh.rmtree(d)
                self._json({'ok': True}); return
            n = int(self.headers.get('Content-Length') or 0)
            try:
                body = json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                body = {}
            if self.path == '/api/save':
                do_save(body.get('intent') or 'Untitled', body.get('project') or '',
                        body.get('note') or '')
                self._json({'ok': True}); return
            if self.path == '/api/projects':
                self._json(add_project(body.get('name', ''))); return
            self.send_error(404)

    # already running? just open it (any HTTP answer means alive)
    try:
        import urllib.request, urllib.error
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/api/saves', timeout=2)
            alive = True
        except urllib.error.HTTPError:
            alive = True
        except Exception:
            alive = False
        if alive:
            print(f'🎮 Panel already running: http://127.0.0.1:{port}')
            if open_browser:
                webbrowser.open(f'http://127.0.0.1:{port}')
            return
    except Exception:
        pass
    srv = None
    for p in (port, port + 1, port + 2):
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', p), H)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        print('Ports 7799-7801 are all in use; stop the old process first.')
        return
    url = f'http://127.0.0.1:{port}'
    print(f'🎮 Quicksave panel: {url}   (Ctrl+C to quit)')
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

# ---------------- main ----------------

def main():
    args = sys.argv[1:]
    if not args:
        if IS_MAC:
            out = osa('choose from list {"📌 新建存档", "⏪ 读档"} with title "Quicksave" OK button name "选它" cancel button name "关闭"', timeout=120)
            if out == '📌 新建存档':
                do_save()
            elif out == '⏪ 读档':
                do_load()
        else:
            print(__doc__)
        return
    cmd = args[0]
    if cmd == 'save':
        intent = args[args.index('-m') + 1] if '-m' in args else None
        note = args[args.index('-n') + 1] if '-n' in args else ''
        project = ''
        if '-p' in args:
            project = args[args.index('-p') + 1]
            add_project(project)
        do_save(intent, project, note)
    elif cmd == 'list':
        do_list()
    elif cmd == 'load':
        idx = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        do_load(idx)
    elif cmd == 'ui':
        run_ui(open_browser='--no-open' not in args)
    else:
        print(__doc__)

if __name__ == '__main__':
    main()
