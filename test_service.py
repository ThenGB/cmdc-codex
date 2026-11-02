"""Linux CI smoke test: start/stop the real user service without inference calls."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.request

if sys.platform != 'linux' or os.environ.get('GITHUB_ACTIONS') != 'true':
    raise SystemExit('This isolated service smoke test runs only on a fresh Linux CI runner.')
spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('cmdc-codex.py'))
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
with tempfile.TemporaryDirectory() as directory:
    installer.CODEX=Path(directory)/'.codex'
    installer.CONFIG=installer.CODEX/'config.toml'
    installer.CODEX.mkdir()
    original='model = "native-model"\n'
    installer.CONFIG.write_text(original)
    installer.getpass.getpass=lambda _: 'fake-ci-key-never-used-for-inference'
    try:
        installer.install()
        with urllib.request.urlopen('http://127.0.0.1:17842/health',timeout=5) as response:
            assert json.load(response)['service']=='codex-commandcode-router-v1'
        assert (installer.ROOT/'credentials.json').stat().st_mode & 0o777 == 0o600
    finally:
        if (installer.ROOT/'install.json').exists():
            installer.uninstall()
    assert installer.tomllib.loads(installer.CONFIG.read_text())['model']=='native-model'
    assert not (installer.HOME/'.config/systemd/user/cmdc-codex.service').exists()
print('Linux systemd service install/start/health/stop/uninstall passed.')
