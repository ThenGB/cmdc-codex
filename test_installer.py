import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('installer',Path(__file__).with_name('cmdc-codex.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class InstallerTests(unittest.TestCase):
    def test_safe_roundtrip_and_unrelated_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            installer.HOME=home
            installer.ROOT=home/'runtime'
            installer.CODEX=home/'.codex'
            installer.CONFIG=installer.CODEX/'config.toml'
            installer.CODEX.mkdir()
            original='openai_base_url = "http://127.0.0.1:17841/v1"\nmodel = "native-model"\n[desktop]\nenabled-reasoning-efforts = ["low", "high"]\n'
            installer.CONFIG.write_text(original)
            auth=home/'.commandcode/auth.json'
            auth.parent.mkdir()
            auth.write_text(json.dumps({'apiKey':'test-placeholder-only'}))
            installer.install(no_service=True)
            self.assertFalse((installer.ROOT/'credentials.json').exists())
            installed=installer.CONFIG.read_text()
            self.assertEqual(installer.tomllib.loads(installed)['model'],'native-model')
            installer.CONFIG.write_text(installed+'\n[features]\nmulti_agent = true\n')
            installer.uninstall()
            restored=installer.CONFIG.read_text()
            self.assertIn(original,restored)
            self.assertIn('multi_agent = true',restored)
            self.assertTrue(auth.exists())

    def test_conflicting_change_is_not_overwritten(self):
        text,change=installer.edit_setting('model = "native"\n','','openai_base_url','"http://localhost:17842/v1"')
        with self.assertRaises(RuntimeError):
            installer.restore_setting(text.replace('17842','19999'),change)

    def test_nested_route_is_not_changed(self):
        source='[other]\nopenai_base_url = "leave me"\n'
        changed,change=installer.edit_setting(source,'','openai_base_url','"new"')
        self.assertEqual(installer.tomllib.loads(changed)['other']['openai_base_url'],'leave me')
        self.assertEqual(installer.restore_setting(changed,change),source)

    def test_service_failure_preserves_config_and_can_be_uninstalled(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            installer.HOME=home
            installer.ROOT=home/'runtime'
            installer.CODEX=home/'.codex'
            installer.CONFIG=installer.CODEX/'config.toml'
            installer.CODEX.mkdir()
            original='model = "native-model"\n'
            installer.CONFIG.write_text(original)
            auth=home/'.commandcode/auth.json'
            auth.parent.mkdir()
            auth.write_text(json.dumps({'apiKey':'test-placeholder-only'}))
            real_service=installer.service
            def fail_start(start,*_):
                if start:
                    raise RuntimeError('Service unavailable')
            installer.service=fail_start
            try:
                with self.assertRaises(RuntimeError):
                    installer.install()
                self.assertEqual(installer.CONFIG.read_text(),original)
                installer.uninstall()
                self.assertEqual(installer.CONFIG.read_text(),original)
                self.assertTrue(auth.exists())
            finally:
                installer.service=real_service

if __name__=='__main__':
    unittest.main()
