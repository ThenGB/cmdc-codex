#!/usr/bin/env python3
"""User-scoped installation; never edits Codex or CommandCode credentials."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.request

NAME = 'cmdc-codex'
BASE = Path(__file__).resolve().parent
HOME = Path.home()
CODEX = Path(os.environ.get('CODEX_HOME', HOME / '.codex'))
ROOT = (CODEX if sys.platform == 'win32' else HOME / '.local/share') / NAME
CONFIG = CODEX / 'config.toml'

def write_json(p, value):
    p.write_text(json.dumps(value, indent=2), encoding='utf-8')

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def edit_setting(text, section, key, value):
    lines = text.splitlines(keepends=True)
    active = ''
    end = len(lines)
    found_section = not section
    for i, line in enumerate(lines):
        match = re.match(r'^\s*\[([^\]]+)\]', line)
        if match:
            if active == section:
                end = i
            active = match[1]
            if active == section:
                found_section = True
        if active == section and re.match(r'^\s*' + re.escape(key) + r'\s*=', line):
            replacement = key + ' = ' + value + '\n'
            lines[i] = replacement
            return ''.join(lines), {'section':section, 'key':key, 'before':line, 'after':replacement}
    if not found_section:
        lines += ['\n[' + section + ']\n']
        end = len(lines)
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'
    replacement = key + ' = ' + value + '\n'
    lines.insert(end, replacement)
    return ''.join(lines), {'section':section, 'key':key, 'before':None, 'after':replacement}

def restore_setting(text, change):
    lines = text.splitlines(keepends=True)
    active = ''
    for i, line in enumerate(lines):
        match = re.match(r'^\s*\[([^\]]+)\]', line)
        if match:
            active = match[1]
        if active == change['section'] and re.match(r'^\s*' + re.escape(change['key']) + r'\s*=', line):
            if line != change['after']:
                raise RuntimeError('Setting changed after installation; refusing to overwrite: ' + change['key'])
            lines[i:i+1] = [change['before']] if change['before'] is not None else []
            return ''.join(lines)
    raise RuntimeError('Managed setting is missing: ' + change['key'])

def ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"

def service(install, root, node):
    script = root / 'router.cjs'
    if sys.platform == 'win32':
        executable = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        invocation = '& ' + ps_quote(node) + ' ' + ps_quote(script) + ' >> ' + ps_quote(root/'service.log') + ' 2>&1'
        arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -Command "' + invocation + '"'
        # A named, user-scoped scheduled task provides explicit start/stop ownership.
        if install:
            code = ("$ErrorActionPreference='Stop'; $existing=Get-ScheduledTask -TaskName 'cmdc-codex' -ErrorAction SilentlyContinue; "
                    "if($existing){throw 'Task cmdc-codex already exists'}; "
                    "$action=New-ScheduledTaskAction -Execute " + ps_quote(executable) + " -Argument " + ps_quote(arguments) + "; "
                    "$trigger=New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name); "
                    "$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
                    "Register-ScheduledTask -TaskName 'cmdc-codex' -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited | Out-Null; "
                    "Start-ScheduledTask -TaskName 'cmdc-codex'")
        else:
            code = ("$ErrorActionPreference='Stop'; $task=Get-ScheduledTask -TaskName 'cmdc-codex' -ErrorAction SilentlyContinue; "
                    "if($task){if($task.Actions.Arguments -ne " + ps_quote(arguments) + "){throw 'Task ownership changed'}; "
                    "Stop-ScheduledTask -TaskName 'cmdc-codex'; Unregister-ScheduledTask -TaskName 'cmdc-codex' -Confirm:$false}")
        subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code], check=True)
    else:
        unit = HOME / '.config/systemd/user/cmdc-codex.service'
        expected = '[Unit]\nDescription=CommandCode and native Codex model router\n[Service]\nExecStart="' + str(node) + '" "' + str(script) + '"\nRestart=on-failure\n[Install]\nWantedBy=default.target\n'
        if install:
            if unit.exists():
                raise RuntimeError('Systemd unit already exists')
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_text(expected, encoding='utf-8')
            subprocess.run(['systemctl','--user','daemon-reload'], check=True)
            subprocess.run(['systemctl','--user','enable','--now',NAME], check=True)
        elif unit.exists():
            if unit.read_text(encoding='utf-8') != expected:
                raise RuntimeError('Systemd unit ownership changed')
            subprocess.run(['systemctl','--user','disable','--now',NAME], check=True)
            unit.unlink()
            subprocess.run(['systemctl','--user','daemon-reload'], check=True)

def install(no_service=False):
    if (ROOT / 'install.json').exists():
        raise RuntimeError('Already installed; uninstall safely before reinstalling')
    node = shutil.which('node')
    if not node or int(subprocess.check_output([node,'-p','process.versions.node.split(".")[0]'], text=True)) < 24:
        raise RuntimeError('Node.js 24+ is required (zstd support)')
    original = CONFIG.read_text(encoding='utf-8-sig') if CONFIG.exists() else ''
    data = tomllib.loads(original)
    if data.get('model_provider', 'openai') != 'openai' or data.get('model_catalog_json'):
        raise RuntimeError('Restore native openai provider/catalog first; existing custom configuration is preserved')
    ROOT.mkdir(parents=True, exist_ok=True)
    if ROOT.is_symlink():
        raise RuntimeError('Install directory must not be a symlink')
    auth = HOME / '.commandcode/auth.json'
    detected = json.loads(auth.read_text(encoding='utf-8')).get('apiKey') if auth.exists() else None
    own_key = ROOT / 'credentials.json'
    if not detected:
        key = getpass.getpass('CommandCode API key: ').strip()
        if not key:
            raise RuntimeError('API key is empty')
        fd = os.open(own_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump({'apiKey':key}, handle)
        if sys.platform == 'win32':
            subprocess.run(['icacls', str(own_key), '/inheritance:r', '/grant:r', getpass.getuser()+':F'],check=True,stdout=subprocess.DEVNULL)
    files = []
    for name in ['router.cjs','catalog.cjs']:
        target = ROOT / name
        if target.exists():
            raise RuntimeError('Install file already exists: ' + str(target))
        shutil.copyfile(BASE / 'src' / name, target)
        files.append(name)
    settings = {'port':17842,'nativeBase':data.get('openai_base_url','https://chatgpt.com/backend-api/codex'),'keyFile':str(own_key)}
    write_json(ROOT / 'settings.json', settings)
    (ROOT / 'config.backup.toml').write_text(original,encoding='utf-8')
    text, route = edit_setting(original, '', 'openai_base_url', json.dumps('http://127.0.0.1:17842/v1'))
    efforts = data.get('desktop',{}).get('enabled-reasoning-efforts',['low','medium','high','xhigh','max','ultra'])
    text, effort = edit_setting(text, 'desktop', 'enabled-reasoning-efforts', json.dumps(list(dict.fromkeys(['none']+efforts))))
    tomllib.loads(text)
    journal = {'root':str(ROOT.resolve()),'node':node,'no_service':no_service,'changes':[route,effort],'files':files,'owns_key':not bool(detected)}
    write_json(ROOT / 'install.json', journal)
    try:
        if not no_service:
            service(True, ROOT, node)
            for attempt in range(20):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:17842/health', timeout=2) as response:
                        health=json.load(response)
                    if health.get('service')!='codex-commandcode-router-v1':
                        raise RuntimeError('Router port belongs to another service')
                    break
                except OSError:
                    if attempt==19:
                        raise RuntimeError('Router did not start; config remains unchanged')
                    time.sleep(0.5)
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(text, encoding='utf-8')
    except Exception:
        # Leave the journal and backup for recovery, but never leave a dead route active.
        CONFIG.write_text(original, encoding='utf-8')
        if not no_service:
            service(False, ROOT, node)
        raise
    print('Installed cmdc-codex. Restart Codex; native models remain the default.')

def uninstall():
    journal = json.loads((ROOT / 'install.json').read_text(encoding='utf-8'))
    if ROOT.is_symlink() or ROOT.resolve() != Path(journal['root']):
        raise RuntimeError('Install root ownership changed')
    text = CONFIG.read_text(encoding='utf-8')
    for change in reversed(journal['changes']):
        text = restore_setting(text, change)
    tomllib.loads(text)
    if not journal['no_service']:
        service(False, ROOT, journal['node'])
    CONFIG.write_text(text, encoding='utf-8')
    # Delete only known owned files; leave unrelated files and both original auth stores intact.
    names = journal['files'] + ['settings.json','commandcode-models.json','requests.jsonl','config.backup.toml','install.json']
    if journal['owns_key']:
        names.append('credentials.json')
    for name in names:
        target = ROOT / name
        if target.parent != ROOT or target.is_symlink():
            raise RuntimeError('Unsafe uninstall path')
        target.unlink(missing_ok=True)
    try:
        ROOT.rmdir()
    except OSError:
        pass
    print('Uninstalled; unrelated config edits and existing credentials preserved.')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['install','uninstall','status','refresh'])
    parser.add_argument('--no-service', action='store_true', help='Configure only; for tests or a separately supervised service')
    args = parser.parse_args()
    if args.command == 'install':
        install(args.no_service)
    elif args.command == 'uninstall':
        uninstall()
    else:
        url = 'http://127.0.0.1:17842/' + ('refresh' if args.command=='refresh' else 'health')
        headers = {}
        if args.command == 'refresh':
            auth = json.loads((CODEX / 'auth.json').read_text(encoding='utf-8'))
            headers['Authorization'] = 'Bearer ' + (auth.get('tokens',{}).get('access_token') or auth.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY',''))
        req = urllib.request.Request(url, headers=headers, method='POST' if args.command=='refresh' else 'GET')
        with urllib.request.urlopen(req, timeout=90) as response:
            print(response.read().decode())

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('cmdc-codex:', str(exc), file=sys.stderr)
        sys.exit(1)
