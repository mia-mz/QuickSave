#!/usr/bin/env python3
"""Quicksave — 工作存档点
用法:
  qs save [-m "一句话意图"] [-p 项目名]   存档(不带 -m 会弹输入框)
  qs list                                列出所有存档
  qs load [编号]                          读档(不带编号会弹选择面板)
  qs ui                                   打开图形面板
数据存在 ~/Library/Application Support/Quicksave/,不上传任何内容。
"""
import json, os, re, subprocess, sys, datetime, time
from urllib.parse import unquote

VAULT = os.path.expanduser('~/Library/Application Support/Quicksave/saves')
PROJECTS_FILE = os.path.expanduser('~/Library/Application Support/Quicksave/projects.json')

# ---------------- helpers ----------------

def osa(script, timeout=20):
    """Run AppleScript, return stdout or None."""
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
    """single-quote a path for a shell command embedded in AppleScript"""
    return "'" + path.replace("'", "'\\''") + "'"

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
        os.makedirs(os.path.dirname(PROJECTS_FILE), exist_ok=True)
        json.dump({'projects': lst}, open(PROJECTS_FILE, 'w'), ensure_ascii=False)
    return lst

def _proc_table():
    """pid -> (ppid, command path) for every process."""
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
    try:
        l = subprocess.run(['lsof', '-a', '-p', pid, '-d', 'cwd', '-Fn'],
                           capture_output=True, text=True, timeout=10).stdout
        for ln in l.splitlines():
            if ln.startswith('n'):
                return ln[1:]
    except Exception:
        pass
    return None

# ---------------- capture ----------------

def capture_front():
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
    """app: 'Google Chrome' or 'Safari' -> list of windows, each a list of {url,title}."""
    if not app_running(app):
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
    try:
        out = subprocess.run(['ps', '-ax', '-o', 'pid=,tty=,command='],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return {}
    found = {}
    for line in out.splitlines():
        bits = line.split(None, 2)
        if len(bits) != 3 or not bits[1].startswith('ttys'):
            continue
        pid, _tty, args = bits
        for t in args.split()[:2]:
            base = t.rsplit('/', 1)[-1]
            if base in ('claude', 'codex'):
                found[pid] = base
                break
    return found

def _shell_cwds():
    """cwds of interactive shells, excluding scratch dirs and shells that are
    descendants of an AI agent (claude/codex spawn their own workers)."""
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
        if len(bits) != 3 or not bits[1].startswith('ttys'):
            continue
        pid, _tty, comm = bits
        base = comm.rsplit('/', 1)[-1].lstrip('-')
        if base not in ('zsh', 'bash', 'fish', 'sh'):
            continue
        if any(p in agents for p, _ in _ancestors(pid, table)):
            continue                      # an agent's internal shell, not yours
        pids.append(pid)
    if not pids:
        return []
    try:
        l = subprocess.run(['lsof', '-a', '-p', ','.join(pids), '-d', 'cwd', '-Fn'],
                           capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return []
    cwds, seen = [], set()
    for ln in l.splitlines():
        if ln.startswith('n'):
            c = ln[1:]
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
    app = 'iTerm2' if app_running('iTerm2') else 'Terminal'
    return {'app': app, 'cwds': cwds}

def capture_vscode():
    """VS Code's last-known open workspace folders."""
    p = os.path.expanduser('~/Library/Application Support/Code/User/globalStorage/storage.json')
    try:
        st = json.load(open(p))
        wins = st.get('windowsState', {}).get('openedWindows', [])
        last = st.get('windowsState', {}).get('lastActiveWindow')
        if last:
            wins = [last] + wins
        out, seen = [], set()
        for w in wins:
            uri = (w or {}).get('folder', '')
            if uri.startswith('file://'):
                path = unquote(uri[7:])
                if path and path not in seen and os.path.exists(path):
                    seen.add(path); out.append(path)
        return out
    except Exception:
        return []

def _host_of(pid, table):
    """Which app hosts this process: 'vscode' or 'terminal'."""
    for _pid, comm in _ancestors(pid, table):
        name = comm.rsplit('/', 1)[-1]
        if 'Code Helper' in comm or name in ('Electron', 'Code'):
            return 'vscode'
        if name in ('Terminal', 'iTerm2', 'iTerm', 'WarpTerminal', 'ghostty',
                    'kitty', 'alacritty', 'Hyper'):
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
    """Newest Claude Code session id for a project dir."""
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
    out = osa('''display dialog "此刻你正要干嘛?一句话。" default answer "" with title "Quicksave 存档" buttons {"取消", "存档"} default button "存档"''', timeout=120)
    if not out or 'text returned:' not in out:
        return None
    return out.split('text returned:')[-1].strip()

def do_save(intent=None, project=''):
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
    print(f'   意图: {intent}')
    print(f'   浏览器 {ntabs} 标签 · 终端 {nterm} 目录 · VS Code {len(state["vscode"])} 工作区 · AI 会话 {nag}')
    osa(f'display notification "{esc(intent[:40])}" with title "Quicksave 已存档" subtitle "浏览器 {ntabs} 标签 · AI 会话 {nag}"')
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
        if app == 'Google Chrome':
            lines = ['set w to make new window',
                     f'set URL of active tab of w to "{esc(urls[0])}"']
            for u in urls[1:]:
                lines.append(f'tell w to make new tab with properties {{URL:"{esc(u)}"}}')
            osa('tell application "Google Chrome"\nactivate\n' + '\n'.join(lines) + '\nend tell', timeout=60)
        else:
            lines = ['set d to make new document with properties {URL:"%s"}' % esc(urls[0])]
            for u in urls[1:]:
                lines.append(f'tell front window to set t to make new tab with properties {{URL:"{esc(u)}"}}')
            osa('tell application "Safari"\nactivate\n' + '\n'.join(lines) + '\nend tell', timeout=60)
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
    if term['app'] == 'iTerm2':
        lines = ['set w to create window with default profile',
                 f'tell current session of w to write text "cd {shq(cwds[0])}"']
        for c in cwds[1:]:
            lines.append('tell w')
            lines.append('  set t to create tab with default profile')
            lines.append(f'  tell current session of t to write text "cd {shq(c)}"')
            lines.append('end tell')
        osa('tell application "iTerm2"\nactivate\n' + '\n'.join(lines) + '\nend tell', timeout=60)
    else:
        lines = [f'do script "cd {shq(c)}"' for c in cwds]
        osa('tell application "Terminal"\nactivate\n' + '\n'.join(lines) + '\nend tell', timeout=60)
    return cwds, len(term['cwds']) - len(cwds) + extra

def restore_vscode(folders):
    code = _code_cli()
    for f in folders[:4]:
        if code:
            subprocess.run([code, f], timeout=15)
        else:
            subprocess.run(['open', '-a', 'Visual Studio Code', f])
    return len(folders[:4])

def _code_cli():
    import shutil as _sh
    return (_sh.which('code') or
            ('/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code'
             if os.path.exists('/Applications/Visual Studio Code.app') else None))

def _frontmost():
    return osa('tell application "System Events" to get name of first application process whose frontmost is true')

def _resume_in_vscode(folder, cmd):
    """Open the workspace, wait until VS Code is truly frontmost, then spawn a
    fresh integrated terminal (^⇧`) and paste the resume command.
    Never types unless VS Code owns the keyboard. Returns True on success."""
    code = _code_cli()
    if code:
        subprocess.run([code, folder], timeout=15)
    else:
        subprocess.run(['open', '-a', 'Visual Studio Code', folder])
    ok_front = False
    for _ in range(14):                       # up to ~7s for the window
        if _frontmost() == 'Code':
            ok_front = True; break
        time.sleep(0.5)
    if not ok_front:
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
    Returns (claimed_cwds, detail list)."""
    if not agents:
        return set(), []
    cur = {(a['tool'], a['cwd']) for a in capture_agents()}
    used, detail, term_lines = set(), [], []
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
            cmd = 'codex resume'          # per-session picker, not blind --last
        used.add(cwd)
        if a.get('host') == 'vscode' and _resume_in_vscode(cwd, cmd):
            detail.append((name, 'vscode')); continue
        if a.get('host') == 'vscode':
            detail.append((name, 'terminal-fallback'))
        else:
            detail.append((name, 'terminal'))
        term_lines.append(f'do script "{esc(f"cd {shq(cwd)} && {cmd}")}"')
    if term_lines:
        osa('tell application "Terminal"\nactivate\n' + '\n'.join(term_lines) + '\nend tell', timeout=60)
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

AGENT_WORDS = {'vscode': '已在 VS Code 内置终端接上', 'terminal': '已在 Terminal 接上',
               'terminal-fallback': '进不去 VS Code(缺辅助功能权限),改在 Terminal 接上',
               'already': '本来就开着,没动', 'gone': '项目目录已不存在,跳过'}

def do_load(idx=None, interactive=True):
    saves = all_saves()
    if not saves:
        print('没有存档。'); return None
    if idx is None:
        items = [label(s, st) for s, st in saves[:20]]
        lst = ', '.join('"' + esc(x) + '"' for x in items)
        out = osa(f'choose from list {{{lst}}} with title "Quicksave 读档" with prompt "回到哪个瞬间?" OK button name "读档" cancel button name "算了"', timeout=120)
        if not out or out == 'false':
            print('已取消。'); return None
        try:
            idx = items.index(out) + 1
        except ValueError:
            print('没找到该存档。'); return None
    slug, st = saves[idx - 1]
    print(f'读档: {label(slug, st)}')
    ncode = restore_vscode(st.get('vscode', []))
    used, agent_detail = restore_agents(st.get('agents', []))
    c_open, c_skip = restore_browser('Google Chrome', st.get('chrome', []))
    s_open, s_skip = restore_browser('Safari', st.get('safari', []))
    t_open, t_skip = restore_terminals(st.get('terminals'), exclude=used)
    summary = {
        'intent': st.get('intent') or '(当时没留话)',
        'tabs_opened': c_open + s_open, 'tabs_already': c_skip + s_skip,
        'terms_opened': t_open, 'terms_skipped': t_skip,
        'code': ncode,
        'agents': [{'name': n, 'how': h, 'text': AGENT_WORDS.get(h, h)} for n, h in agent_detail],
    }
    print(f"   补开标签 {summary['tabs_opened']}(已开着 {summary['tabs_already']} 个没动)"
          f" · 终端 {len(t_open)} · VS Code {ncode}")
    for a in summary['agents']:
        print(f"   {a['name']}: {a['text']}")
    if interactive:
        osa(f'display dialog "{esc(summary["intent"])}" with title "你当时说" buttons {{"好"}} default button "好"', timeout=120)
    print('✅ 恢复完成。')
    return summary

# ---------------- web ui ----------------

UI_HTML = r'''<!doctype html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quicksave</title>
<style>
:root{
  --bg:#14161d; --card:#1b1e2a; --card2:#20242f; --line:rgba(214,222,255,.08);
  --ink:#e8ebf5; --dim:#8b91a8; --gold:#f0b849; --gold-dim:rgba(240,184,73,.14);
  --mint:#7ed9a5; --red:#ff8d7a;
  --mono:ui-monospace,"SF Mono",Menlo,monospace;
  --serif:"Songti SC","STSong",serif;
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,#171a23,#12141b 60%,#0e1015);color:var(--ink);
  font:15px/1.7 -apple-system,"PingFang SC",sans-serif;min-height:100vh}
.wrap{max-width:760px;margin:0 auto;padding:44px 24px 80px}
header{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px;gap:16px;flex-wrap:wrap}
.brand .word{font:600 13px var(--mono);letter-spacing:.42em;color:var(--gold);text-transform:uppercase}
.brand h1{margin:2px 0 0;font-size:26px;font-weight:700;letter-spacing:.04em}
.newbtn{
  appearance:none;border:1px solid rgba(240,184,73,.55);background:var(--gold-dim);color:#ffd98f;
  font:600 14px/1 inherit;padding:12px 22px;border-radius:12px;cursor:pointer;letter-spacing:.05em;
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
  font-family:var(--serif);font-size:19px;line-height:1.55;font-weight:700;margin:7px 0 8px;
  overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
}
.intent.none{color:var(--dim);font-weight:400;font-style:italic}
.chips{display:flex;gap:7px;flex-wrap:wrap}
.chip{font-size:11.5px;color:var(--dim);border:1px solid var(--line);background:var(--card2);
  padding:2.5px 9px;border-radius:999px;white-space:nowrap}
.chip b{color:#c9cfdf;font-weight:600}
.acts{display:flex;gap:10px;margin-top:11px;align-items:center}
.load{
  appearance:none;border:0;background:var(--gold);color:#241a05;font:700 13px/1 inherit;
  padding:9px 20px;border-radius:9px;cursor:pointer;letter-spacing:.06em;transition:filter .15s;
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
  display:flex;align-items:flex-start;justify-content:center;padding-top:16vh;z-index:20}
.modal{width:min(92vw,520px);background:var(--card);border:1px solid rgba(240,184,73,.35);
  border-radius:16px;padding:24px;box-shadow:0 30px 80px rgba(0,0,0,.6)}
.modal h2{margin:0 0 4px;font-size:17px}
.modal p{margin:0 0 14px;color:var(--dim);font-size:13px}
.modal .quote{font-family:var(--serif);font-size:19px;font-weight:700;margin:8px 0 12px}
.modal ul{margin:0 0 6px;padding-left:18px;color:var(--dim);font-size:13.5px}
.modal ul b{color:var(--ink)}
.modal input{
  width:100%;font:16px inherit;color:var(--ink);background:var(--card2);
  border:1px solid var(--line);border-radius:10px;padding:12px 14px;outline:none;
}
.modal input:focus{border-color:rgba(240,184,73,.5)}
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
      <h1>工作存档点</h1>
    </div>
    <button class="newbtn" id="newBtn">📌 新建存档</button>
  </header>
  <div class="tabs" id="tabs"></div>
  <div class="slots" id="slots"></div>
  <div class="foot">↑↓ 选择 · <kbd>回车</kbd> 读档 · 终端里 <span style="font-family:var(--mono)">qs save</span> 随时存</div>
</div>
<div class="toast" id="toast"></div>
<script>
const TOKEN='__TOKEN__';
const $=s=>document.querySelector(s);
const api=(url,opt)=>fetch(url,Object.assign({headers:{'X-QS-Token':TOKEN,'Content-Type':'application/json'}},opt||{}));
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
        const name=prompt('新项目叫什么?(比如 PHLLM、Heartlines)');
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
  mk('全部','__all__');
  projects.forEach(p=>mk('📁 '+p,p));
  mk('+ 新建项目','__new__','add');
}
function rel(iso){
  const d=(Date.now()-new Date(iso))/60000;
  if(d<1) return '刚刚';
  if(d<60) return Math.floor(d)+' 分钟前';
  if(d<1440) return Math.floor(d/60)+' 小时前';
  return Math.floor(d/1440)+' 天前';
}
function escH(s){const d=document.createElement('i');d.textContent=s;return d.innerHTML}
async function refresh(){
  const all=await (await api('/api/saves')).json();
  saves=cur==='__all__'?all:all.filter(s=>s.project===cur);
  const box=$('#slots'); box.textContent='';
  if(!saves.length){
    box.innerHTML='<div class="empty"><b>'+(cur==='__all__'?'还没有存档。':'这个项目下还没有存档。')+'</b><br>点右上角新建,或在被打断前按你的热键。</div>';
    return;
  }
  saves.forEach((s,i)=>{
    const el=document.createElement('div');
    el.className='slot'+(i===hi?' hi':'');
    const chips=[];
    if(s.project&&cur==='__all__') chips.push('<span class="chip">📁 <b>'+escH(s.project)+'</b></span>');
    if(s.agents) chips.push('<span class="chip">🤖 <b>'+s.agents+'</b> 个 AI 会话</span>');
    if(s.tabs) chips.push('<span class="chip">浏览器 <b>'+s.tabs+'</b> 标签</span>');
    if(s.terms) chips.push('<span class="chip">终端 <b>'+s.terms+'</b> 目录</span>');
    if(s.code) chips.push('<span class="chip">Code <b>'+s.code+'</b> 工作区</span>');
    if(s.front) chips.push('<span class="chip">当时在 <b>'+escH(s.front)+'</b></span>');
    el.innerHTML=
      '<div class="when"><span class="slotno">SLOT '+String(saves.length-i).padStart(2,'0')+'</span>'+
      '<span class="rel">'+rel(s.ts)+'</span><span>'+s.ts.replace('T',' ')+'</span></div>'+
      '<div class="intent'+(s.intent?'':' none')+'">'+(s.intent?'「'+escH(s.intent)+'」':'当时没留话')+'</div>'+
      '<div class="chips">'+chips.join('')+'</div>'+
      '<div class="acts"><button class="load">⏪ 读档</button><button class="ghost">删除</button></div>';
    el.querySelector('.load').onclick=()=>doLoad(s,el);
    el.querySelector('.ghost').onclick=async e=>{
      e.stopPropagation();
      if(!confirm('删除这个存档?')) return;
      await api('/api/delete/'+s.slug,{method:'POST'});
      refresh();
    };
    box.appendChild(el);
  });
}
async function doLoad(s,el){
  const b=el.querySelector('.load'); b.disabled=true; b.textContent='恢复中…';
  toast('正在把缺的补回来…');
  let r=null;
  try{ r=await (await api('/api/load/'+s.slug,{method:'POST'})).json(); }catch(e){}
  b.disabled=false; b.textContent='⏪ 读档';
  if(!r||!r.summary){ toast('恢复出了点问题,看看终端输出'); return; }
  showResult(r.summary);
}
function showResult(sm){
  const v=document.createElement('div'); v.className='veil';
  let items='<li>补开标签 <b>'+sm.tabs_opened+'</b> 个,已开着的 <b>'+sm.tabs_already+'</b> 个没动</li>'+
    '<li>终端目录 <b>'+sm.terms_opened.length+'</b> 个'+(sm.terms_skipped?'(跳过 '+sm.terms_skipped+' 个已在用的)':'')+'</li>'+
    '<li>VS Code 工作区 <b>'+sm.code+'</b> 个</li>';
  (sm.agents||[]).forEach(a=>{items+='<li>🤖 '+escH(a.name)+':'+escH(a.text)+'</li>';});
  v.innerHTML='<div class="modal"><h2>你当时说</h2>'+
    '<div class="quote">「'+escH(sm.intent)+'」</div><ul>'+items+'</ul>'+
    '<div class="mrow"><button class="load" id="ok">回去干活</button></div></div>';
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
  v.innerHTML='<div class="modal"><h2>此刻你正要干嘛?</h2><p>一句话,给回来的你。'+(proj?'存进「'+escH(proj)+'」。':'存为未分类,想归类先在上面选个项目。')+'</p>'+
    '<input id="mi" maxlength="80" placeholder="比如:正在改 IPCW 权重,怀疑第 3 列单位错了">'+
    '<div class="mrow"><button class="ghost" id="mc">算了</button><button class="load" id="mo">📌 存档</button></div></div>';
  document.body.appendChild(v);
  const inp=v.querySelector('#mi'); inp.focus();
  const go=async()=>{
    const intent=inp.value.trim()||'(无)';
    v.remove(); toast('正在冻结当前状态…');
    await api('/api/save',{method:'POST',body:JSON.stringify({intent,project:proj})});
    toast('✅ 已存档'); refresh();
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
    import secrets, threading
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
                import shutil
                d = os.path.join(VAULT, m.group(1))
                if os.path.isdir(d):
                    shutil.rmtree(d)
                self._json({'ok': True}); return
            n = int(self.headers.get('Content-Length') or 0)
            try:
                body = json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                body = {}
            if self.path == '/api/save':
                do_save(body.get('intent') or '(无)', body.get('project') or '')
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
            print(f'🎮 面板已经在运行: http://127.0.0.1:{port}')
            if open_browser:
                subprocess.run(['open', f'http://127.0.0.1:{port}'])
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
        print('端口 7799–7801 都被占用了,先退出旧的进程再试。')
        return
    url = f'http://127.0.0.1:{port}'
    print(f'🎮 Quicksave 面板: {url}   (Ctrl+C 退出)')
    if open_browser:
        subprocess.run(['open', url])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

# ---------------- main ----------------

def main():
    args = sys.argv[1:]
    if not args:
        out = osa('choose from list {"📌 新建存档", "⏪ 读档"} with title "Quicksave" with prompt "工作存档点" OK button name "选它" cancel button name "关闭"', timeout=120)
        if out == '📌 新建存档':
            do_save()
        elif out == '⏪ 读档':
            do_load()
        return
    cmd = args[0]
    if cmd == 'save':
        intent = None
        project = ''
        if '-m' in args:
            intent = args[args.index('-m') + 1]
        if '-p' in args:
            project = args[args.index('-p') + 1]
            add_project(project)
        do_save(intent, project)
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
