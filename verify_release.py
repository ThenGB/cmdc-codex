"""Verify that a source or archive release contains no runtime credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

FORBIDDEN_NAMES = {
    'auth.json', 'credentials.json', 'settings.json', 'install.json',
    'commandcode-models.json', 'models_cache.json', 'requests.jsonl',
}

def local_credentials():
    credentials = []
    sources = [
        (Path.home() / '.commandcode' / 'auth.json', ('apiKey',)),
        (Path.home() / '.codex' / 'auth.json', ('OPENAI_API_KEY', 'tokens.access_token')),
    ]
    for path, fields in sources:
        try:
            value = json.loads(path.read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            continue
        for field in fields:
            current = value
            for part in field.split('.'):
                current = current.get(part, {}) if isinstance(current, dict) else {}
            if isinstance(current, str) and len(current) >= 12:
                credentials.append(current.encode())
    return credentials

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent
    tracked = subprocess.check_output(['git','ls-files','-z'],cwd=repo).decode().split('\0')
    tracked = [name for name in tracked if name]
    forbidden = [name for name in tracked if Path(name).name in FORBIDDEN_NAMES]
    credentials = local_credentials()
    matches = []
    scanned = 0
    if credentials:
        objects = subprocess.check_output(['git','rev-list','--objects','--all'],cwd=repo,text=True).splitlines()
        for item in objects:
            sha = item.split()[0]
            try:
                content = subprocess.check_output(['git','cat-file','-p',sha],cwd=repo,stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                continue
            scanned += 1
            if any(secret in content for secret in credentials):
                matches.append(item)
    archive_matches = []
    archive_sha256 = None
    if args.archive:
        archive_sha256 = hashlib.sha256(args.archive.read_bytes()).hexdigest()
        with zipfile.ZipFile(args.archive) as package:
            for name in package.namelist():
                if Path(name).name in FORBIDDEN_NAMES:
                    archive_matches.append(name)
                content = package.read(name)
                if any(secret in content for secret in credentials):
                    archive_matches.append(name)
    report = {
        'credentialValuesChecked':len(credentials),
        'gitObjectsScanned':scanned,
        'gitHistoryMatches':len(matches),
        'forbiddenTrackedFiles':forbidden,
        'archiveMatches':len(archive_matches),
        'trackedFiles':len(tracked),
        'archiveSha256':archive_sha256,
    }
    print(json.dumps(report,indent=2))
    if forbidden or matches or archive_matches:
        raise SystemExit('Release sanitization failed')

if __name__ == '__main__':
    main()
