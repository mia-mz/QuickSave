#!/usr/bin/env python3
"""Quicksave — game-style save points for your work
Usage:
  qs save [-m "title"] [-n "notes"] [-p project]   create a save point
  qs list                                          list save points
  qs load [index]                                  restore one
  qs ui                                            open the web panel
  qs doctor                                        check macOS permissions
All data stays local in ~/Library/Application Support/Quicksave (macOS)
or ~/.quicksave (Linux/Windows). Nothing is uploaded.

Platform support:
  macOS   full (Chrome/Safari/Firefox tabs, terminals, VS Code, AI sessions)
  Linux   Chrome/Edge/Brave/Firefox tabs, terminals, VS Code, AI sessions
  Windows Chrome/Edge/Brave/Firefox tabs, terminals, VS Code, AI sessions
On macOS, Chrome and Safari are read live through AppleScript. Elsewhere,
Chromium browsers are read through the DevTools endpoint when one is exposed
and through their session files otherwise. Firefox is read from its session
file on every platform. Windows backends read process state through
PowerShell and the Win32 API and want testing on a real Windows machine.
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

_WIN_PROCS = None

def _win_proc_list():
    """Windows: [{pid, ppid, name, cmd}] via a single PowerShell CIM query.
    Cached for the life of this invocation."""
    global _WIN_PROCS
    if _WIN_PROCS is not None:
        return _WIN_PROCS
    _WIN_PROCS = []
    ps = ('Get-CimInstance Win32_Process | '
          'Select-Object ProcessId,ParentProcessId,Name,CommandLine | '
          'ConvertTo-Json -Compress')
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                             capture_output=True, text=True, timeout=25).stdout
        data = json.loads(out) if out.strip() else []
        if isinstance(data, dict):
            data = [data]
        for d in data:
            _WIN_PROCS.append({
                'pid': str(d.get('ProcessId') or ''),
                'ppid': str(d.get('ParentProcessId') or ''),
                'name': d.get('Name') or '',
                'cmd': d.get('CommandLine') or '',
            })
    except Exception:
        pass
    return _WIN_PROCS

def _win_cwd(pid):
    """Windows: read a process's current directory out of its PEB.
    64-bit Python on 64-bit Windows. Returns None on any failure."""
    try:
        import ctypes, struct
        from ctypes import wintypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        ntdll = ctypes.WinDLL('ntdll', use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k32.ReadProcessMemory.restype = wintypes.BOOL
        PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
        h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, int(pid))
        if not h:
            return None
        try:
            class PBI(ctypes.Structure):
                _fields_ = [("Reserved1", ctypes.c_void_p),
                            ("PebBaseAddress", ctypes.c_void_p),
                            ("Reserved2", ctypes.c_void_p * 2),
                            ("UniqueProcessId", ctypes.c_void_p),
                            ("Reserved3", ctypes.c_void_p)]
            pbi = PBI()
            rl = ctypes.c_ulong()
            if ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi),
                                               ctypes.sizeof(pbi), ctypes.byref(rl)) != 0:
                return None
            if not pbi.PebBaseAddress:
                return None

            def rd(addr, size):
                buf = (ctypes.c_char * size)()
                n = ctypes.c_size_t()
                if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)):
                    return None
                return buf.raw[:n.value]

            d = rd(pbi.PebBaseAddress + 0x20, 8)        # PEB->ProcessParameters
            if not d:
                return None
            params = struct.unpack('<Q', d)[0]
            if not params:
                return None
            us = rd(params + 0x38, 16)                  # CurrentDirectory.DosPath (UNICODE_STRING)
            if not us:
                return None
            length = struct.unpack('<H', us[0:2])[0]
            buf_ptr = struct.unpack('<Q', us[8:16])[0]
            if not length or not buf_ptr:
                return None
            raw = rd(buf_ptr, length)
            if not raw:
                return None
            path = raw.decode('utf-16-le', 'ignore').rstrip('\x00').rstrip('\\')
            return path or None
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None

def _proc_table():
    """pid -> (ppid, command name)."""
    if IS_WIN:
        return {p['pid']: (p['ppid'], p['name']) for p in _win_proc_list() if p['pid']}
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
    if IS_WIN:
        return _win_cwd(pid)
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
            wt = shutil.which('wt')
            if wt:
                args = [wt, '-d', cwd]
                if cmd:
                    args += ['cmd', '/k', cmd]
                subprocess.Popen(args)
            else:
                inner = f'cd /d "{cwd}"' + (f' && {cmd}' if cmd else '')
                subprocess.Popen(['cmd', '/c', 'start', 'cmd', '/k', inner])
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

_AGENT_RE = re.compile(r'(?i)(?:^|[\\/"\s])(claude|codex)(?:\.exe|\.cmd|\.js|\.ps1)?(?:["\s]|$)')

def _chromium_roots():
    """(name, user-data root) for every Chromium-family browser on this OS."""
    if IS_MAC:
        base = os.path.expanduser('~/Library/Application Support')
        cands = [('Chrome', os.path.join(base, 'Google/Chrome')),
                 ('Edge', os.path.join(base, 'Microsoft Edge')),
                 ('Brave', os.path.join(base, 'BraveSoftware/Brave-Browser')),
                 ('Chromium', os.path.join(base, 'Chromium'))]
    elif IS_WIN:
        base = os.environ.get('LOCALAPPDATA', '')
        cands = [('Chrome', os.path.join(base, 'Google', 'Chrome', 'User Data')),
                 ('Edge', os.path.join(base, 'Microsoft', 'Edge', 'User Data')),
                 ('Brave', os.path.join(base, 'BraveSoftware', 'Brave-Browser', 'User Data')),
                 ('Chromium', os.path.join(base, 'Chromium', 'User Data'))]
    else:
        base = os.path.expanduser('~/.config')
        cands = [('Chrome', os.path.join(base, 'google-chrome')),
                 ('Edge', os.path.join(base, 'microsoft-edge')),
                 ('Brave', os.path.join(base, 'BraveSoftware', 'Brave-Browser')),
                 ('Chromium', os.path.join(base, 'chromium'))]
    return [(n, p) for n, p in cands if os.path.isdir(p)]

def _parse_snss(path):
    """Parse a Chromium SNSS session file into windows of live tabs.
    Best effort over an undocumented format. Returns [[{url,title}], ...]."""
    import struct
    try:
        data = open(path, 'rb').read()
    except Exception:
        return []
    if data[:4] != b'SNSS':
        return []
    pos = 8                                   # magic + int32 version
    tabs, wtype = {}, {}
    dead_tabs, dead_wins = set(), set()
    while pos + 3 <= len(data):
        (size,) = struct.unpack_from('<H', data, pos); pos += 2
        if size == 0 or pos + size > len(data):
            break
        cmd = data[pos]
        payload = data[pos + 1:pos + size]
        pos += size
        try:
            if cmd == 0 and len(payload) >= 8:            # SetTabWindow
                w, t = struct.unpack_from('<ii', payload, 0)
                tabs.setdefault(t, {})['win'] = w
            elif cmd == 2 and len(payload) >= 8:          # SetTabIndexInWindow
                t, idx = struct.unpack_from('<ii', payload, 0)
                tabs.setdefault(t, {})['index'] = idx
            elif cmd == 6 and len(payload) >= 16:         # UpdateTabNavigation
                p = payload[4:]                            # skip pickle header
                (tab_id, nav_idx, ulen) = struct.unpack_from('<iiI', p, 0)
                off = 12
                if ulen > 100000 or off + ulen > len(p):
                    continue
                url = p[off:off + ulen].decode('utf-8', 'ignore')
                off += (ulen + 3) & ~3
                title = ''
                if off + 4 <= len(p):
                    (tlen,) = struct.unpack_from('<I', p, off); off += 4
                    if tlen < 50000 and off + tlen * 2 <= len(p):
                        title = p[off:off + tlen * 2].decode('utf-16-le', 'ignore')
                tabs.setdefault(tab_id, {}).setdefault('navs', {})[nav_idx] = (url, title)
            elif cmd == 7 and len(payload) >= 8:          # SetSelectedNavigationIndex
                t, idx = struct.unpack_from('<ii', payload, 0)
                tabs.setdefault(t, {})['sel'] = idx
            elif cmd == 9 and len(payload) >= 8:          # SetWindowType
                w, ty = struct.unpack_from('<ii', payload, 0)
                wtype[w] = ty
            elif cmd == 16 and len(payload) >= 4:         # TabClosed
                dead_tabs.add(struct.unpack_from('<i', payload, 0)[0])
            elif cmd == 17 and len(payload) >= 4:         # WindowClosed
                dead_wins.add(struct.unpack_from('<i', payload, 0)[0])
        except Exception:
            continue
    wins = {}
    for t, info in tabs.items():
        if t in dead_tabs:
            continue
        w = info.get('win')
        if w is None or w in dead_wins:
            continue
        if wtype.get(w, 0) != 0:                          # keep normal windows only
            continue
        navs = info.get('navs') or {}
        if not navs:
            continue
        sel = info.get('sel')
        if sel is None or sel not in navs:
            sel = max(navs)
        url, title = navs[sel]
        if not url.startswith(('http', 'file')):
            continue
        wins.setdefault(w, []).append((info.get('index', 0), {'url': url, 'title': title}))
    out = []
    for w in sorted(wins):
        row = [x[1] for x in sorted(wins[w], key=lambda x: x[0])]
        if row:
            out.append(row)
    return out

def _snss_windows(root):
    """Windows of tabs from the newest Session_ file of each active profile.
    A profile counts as active when its session file was written within the
    last hour. The most recent file overall is always included."""
    import glob
    pats = [os.path.join(root, '*', 'Sessions', 'Session_*'),
            os.path.join(root, 'Sessions', 'Session_*')]
    per_profile = {}
    for pat in pats:
        for f in glob.glob(pat):
            prof = os.path.dirname(os.path.dirname(f))
            cur = per_profile.get(prof)
            if cur is None or os.path.getmtime(f) > os.path.getmtime(cur):
                per_profile[prof] = f
    if not per_profile:
        return []
    now = time.time()
    newest = max(per_profile.values(), key=os.path.getmtime)
    out = []
    for f in per_profile.values():
        if f == newest or now - os.path.getmtime(f) < 3600:
            out += _parse_snss(f)
    return out

def _devtools_windows(root):
    """Tabs via the DevTools endpoint when the browser exposes one."""
    import urllib.request
    port_file = os.path.join(root, 'DevToolsActivePort')
    try:
        port = int(open(port_file).readline().strip())
    except Exception:
        return []
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=2) as r:
            data = json.loads(r.read())
    except Exception:
        return []
    tabs = [{'url': d.get('url', ''), 'title': d.get('title', '')}
            for d in data
            if d.get('type') == 'page' and d.get('url', '').startswith(('http', 'file'))]
    return [tabs] if tabs else []

_BROWSER_TOKENS = {'Chrome': ('chrome',), 'Edge': ('msedge', 'microsoft-edge'),
                   'Brave': ('brave',), 'Chromium': ('chromium',)}

def capture_chromium():
    """Chrome/Edge/Brave/Chromium tabs on Windows and Linux. Skips browsers
    with no running process. DevTools first for accuracy, session files
    otherwise."""
    if IS_WIN:
        names = {p['name'].lower() for p in _win_proc_list()}
    else:
        names = {comm.rsplit('/', 1)[-1].lower() for _pp, comm in _proc_table().values()}
    wins = []
    for name, root in _chromium_roots():
        toks = _BROWSER_TOKENS.get(name, ())
        if not any(any(t in n for t in toks) for n in names):
            continue
        got = _devtools_windows(root)
        if not got:
            got = _snss_windows(root)
        wins += got
    return wins

def _lz4_block_decompress(src):
    """Minimal LZ4 block decoder (pure Python). Enough for Firefox sessions."""
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]; i += 1
        lit = token >> 4
        if lit == 15:
            while i < n:
                b = src[i]; i += 1; lit += b
                if b != 255:
                    break
        out += src[i:i + lit]; i += lit
        if i >= n:
            break
        offset = src[i] | (src[i + 1] << 8); i += 2
        if offset == 0:
            break
        mlen = (token & 0x0F)
        if mlen == 15:
            while i < n:
                b = src[i]; i += 1; mlen += b
                if b != 255:
                    break
        mlen += 4
        start = len(out) - offset
        for k in range(mlen):
            out.append(out[start + k])
    return bytes(out)

def _read_mozlz4(path):
    data = open(path, 'rb').read()
    if data[:8] != b'mozLz40\x00':
        return None
    return _lz4_block_decompress(data[12:])

def _firefox_profile_glob():
    import glob
    if IS_MAC:
        root = os.path.expanduser('~/Library/Application Support/Firefox/Profiles')
    elif IS_WIN:
        root = os.path.join(os.environ.get('APPDATA', ''), 'Mozilla', 'Firefox', 'Profiles')
    else:
        root = os.path.expanduser('~/.mozilla/firefox')
    files = []
    for name in ('sessionstore-backups/recovery.jsonlz4', 'sessionstore.jsonlz4'):
        files += glob.glob(os.path.join(root, '*', name))
    return files

def capture_firefox():
    """Cross-platform Firefox open tabs, from the session recovery file."""
    files = _firefox_profile_glob()
    if not files:
        return []
    newest = max(files, key=lambda f: os.path.getmtime(f))
    try:
        raw = _read_mozlz4(newest)
        data = json.loads(raw)
    except Exception:
        return []
    wins = []
    for w in data.get('windows', []):
        tabs = []
        for t in w.get('tabs', []):
            ents = t.get('entries', [])
            idx = t.get('index', len(ents))
            if 1 <= idx <= len(ents):
                e = ents[idx - 1]
                url = e.get('url', '')
                if url.startswith(('http', 'file')):
                    tabs.append({'url': url, 'title': e.get('title', '')})
        if tabs:
            wins.append(tabs)
    return wins

def _agent_pids(table):
    """pids of running claude/codex CLI processes."""
    if IS_WIN:
        found = {}
        for p in _win_proc_list():
            m = _AGENT_RE.search(p['cmd'])
            if m and p['pid']:
                found[p['pid']] = m.group(1).lower()
        return found
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

_WIN_SHELLS = ('cmd.exe', 'powershell.exe', 'pwsh.exe', 'bash.exe', 'nu.exe')

def _shell_cwds():
    """cwds of interactive shells, excluding scratch dirs and shells that are
    descendants of an AI agent."""
    if IS_WIN:
        table = _proc_table()
        agents = set(_agent_pids(table))
        cwds, seen = [], set()
        for p in _win_proc_list():
            if p['name'].lower() not in _WIN_SHELLS:
                continue
            if any(pp in agents for pp, _ in _ancestors(p['pid'], table)):
                continue
            c = _win_cwd(p['pid'])
            if not c:
                continue
            c = os.path.normpath(c)
            low = c.lower()
            if low in seen or low.endswith('\\system32') or '\\temp' in low or '\\windows' in low:
                continue
            seen.add(low); cwds.append(c)
        return cwds
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

_TERM_NAMES = ('Terminal', 'iTerm2', 'iTerm', 'WarpTerminal', 'ghostty', 'kitty',
               'alacritty', 'Hyper', 'gnome-terminal-server', 'konsole', 'xterm',
               'WindowsTerminal.exe', 'OpenConsole.exe', 'conhost.exe')

def _host_of(pid, table):
    """Scan the whole ancestor chain and prefer VS Code when present, so a
    terminal-host ancestor never masks a VS Code integrated terminal."""
    is_vscode = is_term = False
    for _pid, comm in _ancestors(pid, table):
        name = comm.rsplit('/', 1)[-1]
        if 'Code Helper' in comm or name in ('Electron', 'Code', 'code', 'Code.exe'):
            is_vscode = True
        elif name in _TERM_NAMES:
            is_term = True
    if is_vscode:
        return 'vscode'
    return 'terminal' if is_term else 'terminal'

def capture_agents():
    """Running Claude Code / Codex sessions: tool, cwd, host app, resume id.
    If the same (tool, cwd) runs in more than one host, prefer the VS Code
    one — that is the richer restore target."""
    table = _proc_table()
    by_key = {}
    for pid, tool in _agent_pids(table).items():
        cwd = _cwd_of(pid)
        if not cwd:
            continue
        key = (tool, cwd)
        host = _host_of(pid, table)
        prev = by_key.get(key)
        if prev and (prev['host'] == 'vscode' or host != 'vscode'):
            continue                       # keep existing unless we found a vscode upgrade
        a = {'tool': tool, 'cwd': cwd, 'host': host}
        if tool == 'claude':
            sid = _claude_session_for(cwd)
            if sid:
                a['session'] = sid
        by_key[key] = a
    return list(by_key.values())

def _claude_session_for(cwd):
    base = os.path.expanduser('~/.claude/projects/')
    encs = (cwd.replace('/', '-'),
            re.sub(r'[/.]', '-', cwd),
            cwd.replace('\\', '-').replace('/', '-').replace(':', '-'),
            re.sub(r'[\\/.:]', '-', cwd))
    for enc in encs:
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
        'chrome': capture_browser('Google Chrome') if IS_MAC else capture_chromium(),
        'safari': capture_browser('Safari'),
        'firefox': capture_firefox(),
        'terminals': capture_terminals(),
        'vscode': capture_vscode(),
        'agents': capture_agents(),
    }
    json.dump(state, open(os.path.join(d, 'state.json'), 'w'),
              ensure_ascii=False, indent=1)
    ntabs = (sum(len(w) for w in state['chrome']) + sum(len(w) for w in state['safari'])
             + sum(len(w) for w in state['firefox']))
    nterm = len(state['terminals']['cwds']) if state['terminals'] else 0
    nag = len(state['agents'])
    print(f'✅ 存档完成 {slug}' + (f'  [{project}]' if project else ''))
    print(f'   {intent}')
    print(f'   浏览器 {ntabs} 标签 · 终端 {nterm} 目录 · VS Code {len(state["vscode"])} 工作区 · AI 会话 {nag}')
    if not IS_MAC and ntabs == 0:
        print('   (提示: 本平台通过会话文件采集 Chrome/Edge/Brave/Firefox 标签)')
    notify('Quicksave', intent[:40])
    return slug

# ---------------- restore ----------------

def restore_browser(app, wins):
    """Reopen only the tabs that are gone. Returns (opened, skipped)."""
    if not wins:
        return 0, 0
    if IS_MAC:
        now_open = capture_browser(app)
    elif app == 'Google Chrome':
        now_open = capture_chromium()
    else:
        now_open = []
    current = set()
    for w in now_open:
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

def restore_firefox(wins):
    """Reopen missing Firefox tabs through the default handler. (opened, skipped)."""
    if not wins:
        return 0, 0
    current = set()
    for w in capture_firefox():
        for t in w:
            current.add(t.get('url'))
    opened = skipped = 0
    for w in wins:
        for t in w:
            u = t.get('url', '')
            if not u.startswith(('http', 'file')):
                continue
            if u in current:
                skipped += 1
            else:
                webbrowser.open(u); opened += 1
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

def _ax_ok():
    """True iff the app hosting this process has Accessibility permission.
    Reading a process's window count via System Events requires it."""
    if not IS_MAC:
        return False
    r = osa('tell application "System Events" to tell process "Finder" to return count of windows')
    return r is not None and r.strip().lstrip('-').isdigit()

def _own_top_app():
    """Name of the top-level app that runs this process (the one that must
    hold Accessibility permission), e.g. 'Terminal' or 'Code'."""
    if not IS_MAC:
        return 'your terminal'
    table = _proc_table()
    for _pid, comm in _ancestors(str(os.getppid()), table):
        name = comm.rsplit('/', 1)[-1]
        if 'Code Helper' in comm or name in ('Electron', 'Code', 'code'):
            return 'Code'
        if name in ('Terminal', 'iTerm2', 'iTerm', 'WarpTerminal', 'ghostty',
                    'kitty', 'alacritty', 'Hyper'):
            return name
    return 'your terminal'

def _resume_in_vscode(folder, cmd):
    """Open the workspace and put the resume command into a fresh integrated
    terminal. Returns:
      'auto'   — typed and ran it (needs Accessibility permission)
      'manual' — opened the workspace and copied the command to the clipboard;
                 the user presses ^⇧` then Cmd+V to finish
      'noshow' — VS Code would not come to the front"""
    if not IS_MAC:
        return 'noshow'
    code = _code_cli()
    if code:
        subprocess.run([code, folder], timeout=15)
    else:
        subprocess.run(['open', '-a', 'Visual Studio Code', folder])
    for _ in range(16):
        if _frontmost() == 'Code':
            break
        time.sleep(0.5)
    else:
        return 'noshow'
    # stage the command on the clipboard either way
    old_clip = subprocess.run(['pbpaste'], capture_output=True, text=True).stdout
    subprocess.run(['pbcopy'], input=cmd, text=True)
    if not _ax_ok():
        return 'manual'                    # leave it on the clipboard for the user
    ok = osa('''tell application "System Events"
  set frontmost of first process whose name is "Code" to true
  delay 0.3
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
    return 'auto' if ok == 'ok' else 'manual'

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
        if a.get('host') == 'vscode':
            r = _resume_in_vscode(cwd, cmd)
            if r == 'auto':
                detail.append((name, 'vscode')); continue
            if r == 'manual':
                detail.append((name, 'vscode-paste')); continue
            # 'noshow' — VS Code never fronted; fall through to a terminal
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

def _count_tabs(st):
    return (sum(len(w) for w in st.get('chrome', []))
            + sum(len(w) for w in st.get('safari', []))
            + sum(len(w) for w in st.get('firefox', [])))

def label(slug, st):
    t = datetime.datetime.fromisoformat(st['ts']).strftime('%m-%d %H:%M')
    ntabs = _count_tabs(st)
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

AGENT_WORDS_CN = {'vscode': '已在 VS Code 内置终端自动接上',
                  'vscode-paste': '已打开 VS Code 工作区,命令已在剪贴板,按 ⌃⇧` 开终端后 ⌘V 回车即可',
                  'terminal': '已在终端接上',
                  'terminal-fallback': 'VS Code 没能到前台,改在终端接上',
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
    f_open, f_skip = restore_firefox(st.get('firefox', []))
    t_open, t_skip = restore_terminals(st.get('terminals'), exclude=used)
    summary = {
        'intent': st.get('intent') or '',
        'note': st.get('note') or '',
        'tabs_opened': c_open + s_open + f_open, 'tabs_already': c_skip + s_skip + f_skip,
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
  'vscode-paste':'VS Code workspace opened; command copied — press ^⇧` then ⌘V to finish',
  terminal:'resumed in a terminal window',
  'terminal-fallback':'VS Code did not come forward — resumed in a terminal',
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
                        'tabs': _count_tabs(st),
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
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
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
    elif cmd == 'doctor':
        do_doctor()
    else:
        print(__doc__)

def do_doctor():
    print('Quicksave 自检\n')
    if not IS_MAC:
        print('  平台:', sys.platform, '(非 macOS,自动输入到 VS Code 仅 macOS 支持)')
        return
    app = _own_top_app()
    print(f'  运行 qs 的宿主 app:  {app}')
    autom = _frontmost() is not None
    ax = _ax_ok()
    print(f'  自动化 (Automation): {"✅ 已授权" if autom else "❌ 未授权"}')
    print(f'  辅助功能 (Accessibility): {"✅ 已授权" if ax else "❌ 未授权"}')
    print()
    if ax:
        print('  AI 会话可以全自动接回 VS Code 内置终端。')
    else:
        print(f'  要让 AI 会话自动输入到 VS Code,需要给「{app}」开辅助功能权限:')
        print('  系统设置 → 隐私与安全性 → 辅助功能,把它打开。')
        print('  在没开之前,读档会打开工作区并把命令拷到剪贴板,你在 VS Code')
        print('  里按 ⌃⇧` 开终端、⌘V 回车即可,不会掉进普通终端。')
        print()
        try:
            input('  按回车打开「辅助功能」设置面板(Ctrl+C 跳过)… ')
            subprocess.run(['open',
                'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'])
        except (EOFError, KeyboardInterrupt):
            print()

if __name__ == '__main__':
    main()
