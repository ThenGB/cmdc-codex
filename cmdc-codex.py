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
import tempfile
import tomllib
import urllib.request
import urllib.parse
import urllib.error

NAME = 'cmdc-codex'
BASE = Path(__file__).resolve().parent
HOME = Path.home()
CODEX = Path(os.environ.get('CODEX_HOME', HOME / '.codex'))
ROOT = (CODEX if sys.platform == 'win32' else HOME / '.local/share') / NAME
CONFIG = CODEX / 'config.toml'
COMMANDCODE_API = 'https://api.commandcode.ai'
VERSION = '0.1.4'

def write_json(p, value):
    p.write_text(json.dumps(value, indent=2), encoding='utf-8')

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def write_config(text):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.cmdc-codex-', dir=CONFIG.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        if CONFIG.exists():
            os.chmod(temporary, CONFIG.stat().st_mode)
        os.replace(temporary, CONFIG)
    finally:
        Path(temporary).unlink(missing_ok=True)

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

def configure_gateway(original):
    data = tomllib.loads(original)
    upstream = urllib.parse.urlsplit(data.get('openai_base_url','https://chatgpt.com/backend-api/codex'))
    if upstream.hostname in ('127.0.0.1','localhost','::1') and upstream.port == 17842:
        raise RuntimeError('The router cannot be its own native upstream; restore the original route first')
    if data.get('model_provider', 'openai') != 'openai' or data.get('model_catalog_json'):
        raise RuntimeError('Restore native provider/catalog first; existing custom configuration is preserved')
    if 'cmdc-codex' in data.get('model_providers', {}):
        raise RuntimeError('Provider cmdc-codex already exists; refusing to overwrite it')
    text, provider = edit_setting(original, '', 'model_provider', json.dumps('cmdc-codex'))
    changes = [provider]
    # Existing conversations retain provider=openai when the global provider changes.
    text, legacy_route = edit_setting(text, '', 'openai_base_url', json.dumps('http://127.0.0.1:17842/v1'))
    changes.append(legacy_route)
    for key, value in {'name':'CommandCode + native Codex', 'base_url':'http://127.0.0.1:17842/v1',
                       'wire_api':'responses', 'requires_openai_auth':True, 'supports_websockets':False}.items():
        text, change = edit_setting(text, 'model_providers.cmdc-codex', key, json.dumps(value))
        changes.append(change)
    efforts = data.get('desktop',{}).get('enabled-reasoning-efforts',['low','medium','high','xhigh','max','ultra'])
    text, effort = edit_setting(text, 'desktop', 'enabled-reasoning-efforts', json.dumps(list(dict.fromkeys(['none']+efforts))))
    changes.append(effort)
    tomllib.loads(text)
    return text, changes

def commandcode_auth_paths():
    paths = [HOME / '.commandcode/auth.json']
    if sys.platform == 'win32':
        for name in ('APPDATA', 'LOCALAPPDATA'):
            if os.environ.get(name):
                paths += [Path(os.environ[name]) / 'commandcode/auth.json',
                          Path(os.environ[name]) / 'CommandCode/auth.json']
    elif sys.platform == 'darwin':
        paths.append(HOME / 'Library/Application Support/commandcode/auth.json')
    else:
        config = Path(os.environ.get('XDG_CONFIG_HOME', HOME / '.config'))
        paths.append(config / 'commandcode/auth.json')
    return list(dict.fromkeys(paths))

def detect_commandcode_key():
    environment_key = os.environ.get('COMMANDCODE_API_KEY', '').strip()
    if environment_key:
        return environment_key, 'COMMANDCODE_API_KEY'
    for path in commandcode_auth_paths():
        try:
            key = json.loads(path.read_text(encoding='utf-8-sig')).get('apiKey')
        except (FileNotFoundError, OSError, ValueError, AttributeError):
            continue
        if isinstance(key, str) and key.strip():
            return key.strip(), path
    return None, None

def validate_commandcode_key(key):
    # Provider API keys do not necessarily have account/billing scopes.
    request = urllib.request.Request(COMMANDCODE_API + '/provider/v1/models?view=client',
                                     headers={'Authorization':'Bearer '+key,
                                              'Accept':'application/json',
                                              'User-Agent':'cmdc-codex/'+VERSION})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 200:
                raise RuntimeError('CommandCode login check returned HTTP '+str(response.status))
            json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError('CommandCode login/API key was rejected (HTTP '+str(error.code)+')') from error
    except urllib.error.URLError as error:
        raise RuntimeError('Unable to verify CommandCode login: '+str(error.reason)) from error

def service(install, root, node):
    script = root / 'router.cjs'
    if sys.platform == 'win32':
        journal_path = root / 'install.json'
        journal = json.loads(journal_path.read_text(encoding='utf-8')) if journal_path.exists() else {}
        pythonw = Path(journal.get('pythonw') or Path(sys.executable).with_name('pythonw.exe'))
        executable = str(pythonw)
        invocation = '& ' + ps_quote(node) + ' ' + ps_quote(script) + ' >> ' + ps_quote(root/'service.log') + ' 2>&1'
        legacy_arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -Command "' + invocation + '"'
        direct_arguments = '"' + str(script) + '"'
        arguments = '"' + str(root / 'windows_service.py') + '" "' + str(node) + '"'
        # A named, user-scoped scheduled task provides explicit start/stop ownership.
        if install:
            if not pythonw.is_file():
                raise RuntimeError('pythonw.exe is required for a console-free Windows service')
            code = ("$ErrorActionPreference='Stop'; $existing=Get-ScheduledTask -TaskName 'cmdc-codex' -ErrorAction SilentlyContinue; "
                    "if($existing){throw 'Task cmdc-codex already exists'}; "
                    "$action=New-ScheduledTaskAction -Execute " + ps_quote(executable) + " -Argument " + ps_quote(arguments) + "; "
                    "$trigger=New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name); "
                    "$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1); "
                    "Register-ScheduledTask -TaskName 'cmdc-codex' -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited | Out-Null; "
                    "Start-ScheduledTask -TaskName 'cmdc-codex'")
        else:
            code = ("$ErrorActionPreference='Stop'; $task=Get-ScheduledTask -TaskName 'cmdc-codex' -ErrorAction SilentlyContinue; "
                    "if($task){if($task.Actions.Count -ne 1 -or -not ((($task.Actions.Execute -eq " + ps_quote(executable) + ") -and ($task.Actions.Arguments -eq " + ps_quote(arguments) + ")) -or (($task.Actions.Execute -eq " + ps_quote(node) + ") -and ($task.Actions.Arguments -eq " + ps_quote(direct_arguments) + ")) -or (($task.Actions.Execute -eq 'powershell.exe') -and ($task.Actions.Arguments -eq " + ps_quote(legacy_arguments) + ")))){throw 'Task ownership changed'}; "
                    "Stop-ScheduledTask -TaskName 'cmdc-codex'; Unregister-ScheduledTask -TaskName 'cmdc-codex' -Confirm:$false}; "
                    "$owners=Get-CimInstance Win32_Process -Filter \"Name='node.exe'\"; "
                    "foreach($owner in $owners){if($owner.CommandLine -and $owner.CommandLine.TrimEnd([char]34).EndsWith(" + ps_quote(script) + ",[StringComparison]::OrdinalIgnoreCase)){Stop-Process -Id $owner.ProcessId -Force}}; exit 0")
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
    text, changes = configure_gateway(original)
    detected, source = detect_commandcode_key()
    if detected:
        print('Detected CommandCode login from '+str(source)+'.')
        try:
            validate_commandcode_key(detected)
        except RuntimeError as error:
            print(str(error)+'; enter a current API key.')
            detected = None
            source = None
    if not detected:
        try:
            detected = getpass.getpass('CommandCode API key (input hidden): ').strip()
        except (EOFError, KeyboardInterrupt) as error:
            raise RuntimeError('No CommandCode API key was supplied') from error
        if not detected:
            raise RuntimeError('CommandCode API key is empty')
        validate_commandcode_key(detected)
        print('CommandCode API key validated.')
    ROOT.mkdir(parents=True, exist_ok=True)
    if ROOT.is_symlink():
        raise RuntimeError('Install directory must not be a symlink')
    own_key = ROOT / 'credentials.json'
    should_store_key = source is None or source == 'COMMANDCODE_API_KEY'
    if should_store_key:
        key = detected
        fd = os.open(own_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump({'apiKey':key}, handle)
        if sys.platform == 'win32':
            subprocess.run(['icacls', str(own_key), '/inheritance:r', '/grant:r', getpass.getuser()+':F'],check=True,stdout=subprocess.DEVNULL)
    files = []
    for name in ['router.cjs','catalog.cjs','multimodal.cjs']:
        target = ROOT / name
        if target.exists():
            raise RuntimeError('Install file already exists: ' + str(target))
        shutil.copyfile(BASE / 'src' / name, target)
        files.append(name)
    if sys.platform == 'win32':
        if (ROOT / 'windows_service.py').exists():
            raise RuntimeError('Install file already exists: ' + str(ROOT / 'windows_service.py'))
        shutil.copyfile(BASE / 'windows_service.py', ROOT / 'windows_service.py')
        files.append('windows_service.py')
    settings = {'port':17842,'nativeBase':data.get('openai_base_url','https://chatgpt.com/backend-api/codex'),'keyFile':str(own_key)}
    write_json(ROOT / 'settings.json', settings)
    (ROOT / 'config.backup.toml').write_text(original,encoding='utf-8')
    journal = {'root':str(ROOT.resolve()),'node':node,'no_service':no_service,'changes':changes,'files':files,'owns_key':should_store_key,'status':'prepared'}
    if sys.platform == 'win32':
        journal['pythonw'] = str(Path(sys.executable).with_name('pythonw.exe'))
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
        write_config(text)
        journal['status']='installed'
        write_json(ROOT / 'install.json',journal)
    except Exception:
        # Leave the journal and backup for recovery, but never leave a dead route active.
        write_config(original)
        journal['status']='failed'
        write_json(ROOT / 'install.json',journal)
        if not no_service:
            service(False, ROOT, node)
        raise
    print('Installed cmdc-codex. Restart Codex; native models remain the default.')

def verify_gateway():
    with urllib.request.urlopen('http://127.0.0.1:17842/health', timeout=5) as response:
        if json.load(response).get('service') != 'codex-commandcode-router-v1':
            raise RuntimeError('Router port belongs to another service')
    auth = json.loads((CODEX / 'auth.json').read_text(encoding='utf-8-sig'))
    token = auth.get('tokens',{}).get('access_token') or auth.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY')
    if not token:
        raise RuntimeError('Sign in to Codex before activating the gateway')
    headers = {'Authorization':'Bearer '+token}
    account = auth.get('tokens',{}).get('account_id')
    if account:
        headers['ChatGPT-Account-ID'] = account
    url = 'http://127.0.0.1:17842/v1/models'
    cache = CODEX / 'models_cache.json'
    if cache.exists():
        version = json.loads(cache.read_text(encoding='utf-8-sig')).get('client_version','')
        if re.fullmatch(r'\d+\.\d+\.\d+',version):
            url += '?client_version=' + version
    request = urllib.request.Request(url,headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        models = json.load(response).get('models',[])
    external = [m for m in models if m.get('slug','').startswith('commandcode/')]
    if not external or len(external) == len(models):
        raise RuntimeError('Native and CommandCode catalogs must both be available before activation')

def activate():
    """Recover an existing installation after a manually removed routing config."""
    journal_path = ROOT / 'install.json'
    journal = json.loads(journal_path.read_text(encoding='utf-8-sig'))
    if ROOT.is_symlink() or ROOT.resolve() != Path(journal['root']):
        raise RuntimeError('Install root ownership changed')
    original = CONFIG.read_text(encoding='utf-8-sig') if CONFIG.exists() else ''
    text, changes = configure_gateway(original)
    data = tomllib.loads(original)
    settings = json.loads((ROOT/'settings.json').read_text(encoding='utf-8-sig'))
    if settings['nativeBase'] != data.get('openai_base_url','https://chatgpt.com/backend-api/codex'):
        raise RuntimeError('Native upstream changed; review the existing route before recovery')
    verify_gateway()
    # Record the current user config as the recovery baseline, rather than resurrecting an old backup.
    backup = ROOT / 'config.backup.toml'
    previous_backup = backup.read_text(encoding='utf-8')
    updated = {**journal,'changes':changes,'status':'installed'}
    try:
        backup.write_text(original,encoding='utf-8')
        write_json(journal_path,updated)
        write_config(text)
    except Exception:
        write_config(original)
        backup.write_text(previous_backup,encoding='utf-8')
        write_json(journal_path,journal)
        raise
    print('Gateway activated after native and CommandCode catalog checks. Restart Codex Desktop to load its new provider.')

def uninstall():
    journal = json.loads((ROOT / 'install.json').read_text(encoding='utf-8'))
    if ROOT.is_symlink() or ROOT.resolve() != Path(journal['root']):
        raise RuntimeError('Install root ownership changed')
    text = CONFIG.read_text(encoding='utf-8')
    if journal.get('status','installed')=='installed':
        for change in reversed(journal['changes']):
            text = restore_setting(text, change)
    tomllib.loads(text)
    write_config(text)
    journal['status']='config_restored'
    write_json(ROOT / 'install.json',journal)
    if not journal['no_service']:
        service(False, ROOT, journal['node'])
    # Delete only known owned files; leave unrelated files and both original auth stores intact.
    names = journal['files'] + ['settings.json','commandcode-models.json','commandcode-models.json.tmp','requests.jsonl','service.log','config.backup.toml','install.json']
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
    parser.add_argument('command', choices=['install','activate','uninstall','status','refresh'])
    parser.add_argument('--no-service', action='store_true', help='Configure only; for tests or a separately supervised service')
    args = parser.parse_args()
    if args.command == 'install':
        install(args.no_service)
    elif args.command == 'activate':
        activate()
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
