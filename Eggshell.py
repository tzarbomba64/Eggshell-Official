#!/usr/bin/env python3
import requests
import sys
import os
import zipfile
import io
import tempfile
import shutil
import re
import json
import uuid

# Base directory of the Eggshell script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = 'data.json'

class Eggshell:
    def __init__(self):
        self.debug = False
        self.commands = {}
        self.modules = {}
        self.load_debug_config()
        # register built-in commands
        self.commands['debug'] = self.toggle_debug
        self.commands['update'] = self.do_update
        self.commands['powershell'] = self.do_powershell
        # load all .egg/.EGG files recursively from the script directory
        self.load_all_eggs()

    def load_debug_config(self):
        if not os.path.isfile(DATA_FILE):
            with open(DATA_FILE, 'w') as f:
                json.dump({'debug': False}, f)
            self.debug = False
        else:
            try:
                with open(DATA_FILE, 'r') as f:
                    data = json.load(f)
                self.debug = bool(data.get('debug', False))
            except Exception:
                self.debug = False
                with open(DATA_FILE, 'w') as f:
                    json.dump({'debug': False}, f)

    def toggle_debug(self, *args):
        self.debug = not self.debug
        with open(DATA_FILE, 'w') as f:
            json.dump({'debug': self.debug}, f)
        print(f"Debug mode {'enabled' if self.debug else 'disabled'}.")

    def load_all_eggs(self):
        # preserve built-ins
        builtins = {k: self.commands[k] for k in ('debug', 'update', 'powershell')}
        self.commands.clear()
        self.commands.update(builtins)
        self.modules.clear()

        egg_files = []
        for root, _, files in os.walk(BASE_DIR):
            for fname in files:
                if fname.lower().endswith('.egg'):
                    egg_files.append(os.path.join(root, fname))
        if self.debug:
            print(f"Found egg files: {egg_files}")

        for path in egg_files:
            try:
                with open(path, 'r') as f:
                    text = f.read()
            except Exception as e:
                if self.debug:
                    print(f"Error reading {path}: {e}")
                continue

            name, code_str, cmd_map = self.parse_egg(text)
            if not name or not cmd_map:
                continue
            if self.debug:
                print(f"Loading egg {path}: name={name}, commands={cmd_map}")

            ns = {}
            try:
                exec(code_str, ns)
            except Exception as e:
                if self.debug:
                    print(f"Error executing code in {path}: {e}")
                continue

            for cmd_name, func_name in cmd_map.items():
                func = ns.get(func_name)
                if callable(func):
                    self.commands[cmd_name] = func
                    if self.debug:
                        print(f"Registered '{cmd_name}' from {name}")
                else:
                    if self.debug:
                        print(f"Function '{func_name}' not found in {path}")
            self.modules[name] = ns

    def run(self):
        while True:
            try:
                line = input('eggshell> ').strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            self.handle_command(line)

    def handle_command(self, line):
        parts = line.split()
        cmd, args = parts[0], parts[1:]
        if cmd == 'exit':
            sys.exit(0)
        elif cmd == 'imp':
            self.do_imp(args)
        elif cmd == 'run':
            self.do_run(args)
        elif cmd in self.commands:
            try:
                result = self.commands[cmd](*args)
                if result is not None:
                    print(result)
            except Exception as e:
                print(f"Error running '{cmd}': {e}")
        else:
            print(f"Unknown command: {cmd}")

    def do_imp(self, args):
        if len(args) != 2:
            print('Usage: imp <github_user> <github_repo>')
            return
        user, repo = args
        api_url = f'https://api.github.com/repos/{user}/{repo}'
        r = requests.get(api_url)
        if r.status_code != 200:
            print(f"Repo not found: {user}/{repo}")
            return
        branch = r.json().get('default_branch', 'main')
        zip_url = f'https://github.com/{user}/{repo}/archive/refs/heads/{branch}.zip'
        rzip = requests.get(zip_url)
        if rzip.status_code != 200:
            print(f"Download failed: {rzip.status_code}")
            return
        with tempfile.TemporaryDirectory() as tmp:
            try:
                with zipfile.ZipFile(io.BytesIO(rzip.content)) as zf:
                    zf.extractall(tmp)
            except zipfile.BadZipFile:
                print('Invalid archive')
                return
            extracted = next((d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))), None)
            if not extracted:
                print('No extracted content')
                return
            src = os.path.join(tmp, extracted)
            dest = os.path.join(BASE_DIR, repo)
            if os.path.exists(dest):
                shutil.rmtree(dest)
            shutil.move(src, dest)
        with open(os.path.join(dest, 'URL.txt'), 'w') as f:
            f.write(f'https://github.com/{user}/{repo}')
        print(f"Imported: {repo}")
        self.load_all_eggs()

    def do_update(self, args):
        if len(args) != 2:
            print('Usage: update <github_user> <github_repo>')
            return
        user, repo = args
        dest = os.path.join(BASE_DIR, repo)
        if not os.path.isdir(dest):
            print(f"Not imported: {repo}")
            return
        url_file = os.path.join(dest, 'URL.txt')
        url = open(url_file).read().strip() if os.path.isfile(url_file) else f'https://github.com/{user}/{repo}'
        api = requests.get(f'https://api.github.com/repos/{user}/{repo}')
        if api.status_code != 200:
            print('Repo not found')
            return
        branch = api.json().get('default_branch', 'main')
        rzip = requests.get(f'https://github.com/{user}/{repo}/archive/refs/heads/{branch}.zip')
        if rzip.status_code != 200:
            print('Update download failed')
            return
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(io.BytesIO(rzip.content)) as zf:
                zf.extractall(tmp)
            extracted = os.path.join(tmp, os.listdir(tmp)[0])
            for rdir, _, files in os.walk(extracted):
                for fname in files:
                    rel = os.path.relpath(os.path.join(rdir, fname), extracted)
                    dst = os.path.join(dest, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    if rel.startswith('update' + os.sep) and os.path.exists(dst):
                        orig = open(dst).read().splitlines()
                        with open(os.path.join(rdir, fname)) as sf, open(dst, 'a') as df:
                            for line in sf:
                                if line.rstrip() not in orig:
                                    df.write(line)
                    else:
                        shutil.copy2(os.path.join(rdir, fname), dst)
        print(f"Updated: {repo}")

    def do_powershell(self, *args):
        local = os.environ.get('LOCALAPPDATA', '')
        candidates = [
            os.path.join(local, 'Packages', 'Microsoft.WindowsTerminal_8wekyb3d8bbwe', 'LocalState', 'settings.json'),
            os.path.join(local, 'Packages', 'Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe', 'LocalState', 'settings.json'),
            os.path.join(os.environ.get('USERPROFILE', ''), 'AppData', 'Local', 'Microsoft', 'Windows Terminal', 'settings.json')
        ]
        cfg = next((p for p in candidates if os.path.isfile(p)), None)
        if not cfg:
            print('settings.json not found')
            return
        conf = json.load(open(cfg, encoding='utf-8'))
        profs = conf.get('profiles', {}).get('list', [])
        if any(p.get('name') == 'Eggshell' for p in profs):
            print('Profile exists')
            return
        profs.append({
            'guid': f'{{{uuid.uuid4()}}}',
            'name': 'Eggshell',
            'commandline': f'python "{os.path.abspath(sys.argv[0])}"',
            'startingDirectory': '%USERPROFILE%',
            'icon': os.path.abspath('998d1a7a-f190-4c93-bb32-5fccd08e3080.png'),
            'hidden': False
        })
        conf['profiles']['list'] = profs
        with open(cfg, 'w', encoding='utf-8') as f:
            json.dump(conf, f, indent=2)
        print(f"Added profile to {cfg}")

    def parse_egg(self, text):
        """
        Parse a .egg file text:
        - Extract name from 'name = ...'
        - Extract Python code between parentheses, ending at 'CODE = ended' then ')'
        - Extract commands after the 'commands' section
        """
        name = None
        commands = {}
        lines = text.splitlines()
        code_lines = []
        in_code = False
        code_ended = False
        for line in lines:
            s = line.strip()
            if name is None:
                m = re.match(r'^name\s*=\s*(.+)', s, re.IGNORECASE)
                if m:
                    name = m.group(1).strip()
            if not in_code and '(' in s:
                in_code = True
                remainder = line[line.find('(')+1:]
                if remainder.strip() and remainder.strip() != 'CODE = ended':
                    code_lines.append(remainder)
                continue
            if in_code and not code_ended:
                if s == 'CODE = ended':
                    code_ended = True
                    continue
                code_lines.append(line)
                continue
            if in_code and code_ended and ')' in s:
                in_code = False
                continue
        code_str = '\n'.join(code_lines)
        lower = [l.strip().lower() for l in lines]
        if 'commands' in lower:
            idx = lower.index('commands')
            for cmd_line in lines[idx+1:]:
                cl = cmd_line.strip()
                if '=' in cl:
                    c, f = cmd_line.split('=', 1)
                    commands[c.strip()] = f.strip()
                elif cl:
                    break
        return name, code_str, commands

    def do_run(self, args):
        if len(args) != 1:
            print("Usage: run <script.egsh>")
            return
        path = args[0]
        if not os.path.isfile(path):
            print(f"No such file: {path}")
            return
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    print(f"> {ln}")
                    self.handle_command(ln)

if __name__ == '__main__':
    Eggshell().run()
