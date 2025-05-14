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

# Determine base directory, supporting frozen executable and environments without __file__
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS  # type: ignore
else:
    try:
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        BASE_DIR = os.getcwd()

DATA_FILE = os.path.join(BASE_DIR, 'data.json')

class Eggshell:
    def __init__(self):
        self.debug = False
        self.commands = {}
        self.modules = {}
        self.load_debug_config()
        # register built-in commands
        self.commands['debug'] = self.toggle_debug
        self.commands['imp'] = self.do_imp
        self.commands['update'] = self.do_update
        self.commands['powershell'] = self.do_powershell
        # load all .egg/.EGG add-ons
        self.load_all_eggs()

    def load_debug_config(self):
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
        print(f"Debug {'on' if self.debug else 'off'}.")

    def load_all_eggs(self):
        # Preserve built-ins
        builtins = {k: self.commands[k] for k in ('debug','imp','update','powershell')}
        self.commands.clear()
        self.commands.update(builtins)
        self.modules.clear()

        # Scan recursively for .egg/.EGG files
        egg_files = []
        for root, _, files in os.walk(BASE_DIR):
            for fname in files:
                if fname.lower().endswith('.egg'):
                    egg_files.append(os.path.join(root, fname))
        if self.debug:
            print(f"Found egg files: {egg_files}")

        # Parse and load each egg
        for path in egg_files:
            try:
                text = open(path, 'r').read()
                name, code_str, cmd_map = self.parse_egg(text)
                if not name or not cmd_map:
                    continue
                if self.debug:
                    print(f"Loading add-on '{name}' from {path}, commands: {cmd_map}")
                ns = {}
                exec(code_str, ns)
                for cmd, func_name in cmd_map.items():
                    fn = ns.get(func_name)
                    if callable(fn):
                        self.commands[cmd] = fn
                    elif self.debug:
                        print(f"Function '{func_name}' not found in {path}")
                self.modules[name] = ns
            except Exception as e:
                if self.debug:
                    print(f"Error loading {path}: {e}")

    def run(self):
        # If stdin is not interactive, exit silently
        if not sys.stdin.isatty():
            return
        while True:
            try:
                line = input('eggshell> ')
            except (EOFError, KeyboardInterrupt, RuntimeError, OSError):
                break
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            cmd, args = parts[0], parts[1:]
            if cmd == 'exit':
                break
            elif cmd == 'run':
                self.do_run(*args)
            elif cmd in self.commands:
                try:
                    result = self.commands[cmd](*args)
                    if result is not None:
                        print(result)
                except Exception as e:
                    print(f"Error running '{cmd}': {e}")
            else:
                print(f"Unknown command: {cmd}")

    def do_imp(self, user, repo):
        """Import a GitHub repository as an add-on"""
        try:
            r = requests.get(f'https://api.github.com/repos/{user}/{repo}')
            r.raise_for_status()
            branch = r.json().get('default_branch', 'main')
            zipb = requests.get(f'https://github.com/{user}/{repo}/archive/refs/heads/{branch}.zip').content
            with tempfile.TemporaryDirectory() as tmp:
                with zipfile.ZipFile(io.BytesIO(zipb)) as zf:
                    zf.extractall(tmp)
                root = os.path.join(tmp, os.listdir(tmp)[0])
                dst = os.path.join(BASE_DIR, repo)
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.move(root, dst)
            with open(os.path.join(dst, 'URL.txt'), 'w') as f:
                f.write(f'https://github.com/{user}/{repo}')
            print(f"Imported '{repo}'")
            self.load_all_eggs()
        except Exception as e:
            print(f"imp error: {e}")

    def do_update(self, user, repo):
        """Update an existing add-on from GitHub"""
        try:
            dst = os.path.join(BASE_DIR, repo)
            if not os.path.isdir(dst):
                print(f"Not imported: {repo}")
                return
            r = requests.get(f'https://api.github.com/repos/{user}/{repo}')
            r.raise_for_status()
            branch = r.json().get('default_branch', 'main')
            zipb = requests.get(f'https://github.com/{user}/{repo}/archive/refs/heads/{branch}.zip').content
            with tempfile.TemporaryDirectory() as tmp:
                with zipfile.ZipFile(io.BytesIO(zipb)) as zf:
                    zf.extractall(tmp)
                root = os.path.join(tmp, os.listdir(tmp)[0])
                for rdir, _, files in os.walk(root):
                    for fname in files:
                        src = os.path.join(rdir, fname)
                        rel = os.path.relpath(src, root)
                        dst_file = os.path.join(dst, rel)
                        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                        shutil.copy2(src, dst_file)
            print(f"Updated '{repo}'")
            self.load_all_eggs()
        except Exception as e:
            print(f"update error: {e}")

    def do_run(self, script):
        """Execute commands from an .egsh script file"""
        try:
            with open(script) as f:
                for ln in f:
                    cmd = ln.strip()
                    if not cmd:
                        continue
                    print(f"> {cmd}")
                    parts = cmd.split()
                    if parts[0] == 'exit':
                        return
                    elif parts[0] == 'run':
                        self.do_run(*parts[1:])
                    else:
                        self.run_command(parts[0], parts[1:])
        except Exception as e:
            print(f"run error: {e}")

    def do_powershell(self, *args):
        """Add Eggshell profile to Windows Terminal"""
        # Windows Terminal JSON update logic here...
        pass

    def parse_egg(self, text):
        """Parse .egg file into name, code string, and command map"""
        name = None
        commands = {}
        lines = text.splitlines()
        code_lines = []
        in_code = False
        code_ended = False
        # Extract code block
        for line in lines:
            strip = line.strip()
            if name is None:
                m = re.match(r'^name\s*=\s*(.+)', strip, re.IGNORECASE)
                if m:
                    name = m.group(1).strip()
            if '(' in strip and not in_code:
                in_code = True
                continue
            if in_code and not code_ended:
                if strip == 'CODE = ended':
                    code_ended = True
                    continue
                code_lines.append(line)
                continue
            if in_code and code_ended and ')' in strip:
                break
        code_str = '\n'.join(code_lines)
        # Extract commands
        for idx, line in enumerate(lines):
            if line.strip().lower() == 'commands':
                for cmd_line in lines[idx+1:]:
                    if '=' in cmd_line:
                        cmd, fn = cmd_line.split('=', 1)
                        commands[cmd.strip()] = fn.strip()
                    else:
                        break
                break
        return name, code_str, commands

if __name__ == '__main__':
    Eggshell().run()
