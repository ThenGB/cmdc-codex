import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer',Path(__file__).with_name('cmdc-codex.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class InstallerTests(unittest.TestCase):
    def test_reactivate_after_manual_config_reset_keeps_current_user_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            installer.HOME=home;installer.ROOT=home/'runtime';installer.CODEX=home/'.codex';installer.CONFIG=installer.CODEX/'config.toml'
            installer.CODEX.mkdir()
            original='model = "native-model"\n'
            installer.CONFIG.write_text(original)
            auth=home/'.commandcode/auth.json';auth.parent.mkdir();auth.write_text(json.dumps({'apiKey':'test-placeholder-only'}))
            installer.install(no_service=True)
            reset=original+'[desktop]\nfollowUpQueueMode = "queue"\n'
            installer.CONFIG.write_text(reset)
            self.assertTrue(callable(getattr(installer,'activate',None)), 'Recovery must support a manually reset config')
            with patch.object(installer,'verify_gateway',side_effect=OSError('service down')):
                with self.assertRaises(OSError):installer.activate()
            self.assertEqual(installer.CONFIG.read_text(),reset)
            with patch.object(installer,'verify_gateway',return_value=None):installer.activate()
            data=installer.tomllib.loads(installer.CONFIG.read_text())
            self.assertEqual(data['model_provider'],'cmdc-codex')
            self.assertEqual(data['desktop']['followUpQueueMode'],'queue')
            installer.uninstall()
            self.assertIn(reset,installer.CONFIG.read_text())

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
            effective=installer.tomllib.loads(installed)
            self.assertEqual(effective['model'],'native-model')
            # Desktop's active account whitelist hides foreign IDs on the built-in provider.
            # A real gateway provider must expose the live catalog without replacing auth.
            self.assertEqual(effective.get('model_provider','openai'),'cmdc-codex')
            gateway=effective['model_providers']['cmdc-codex']
            self.assertTrue(gateway['requires_openai_auth'])
            self.assertFalse(gateway['supports_websockets'])
            self.assertEqual(gateway['base_url'],'http://127.0.0.1:17842/v1')
            self.assertNotIn('model_catalog_json',effective)
            self.assertEqual(effective['openai_base_url'],'http://127.0.0.1:17842/v1')
            self.assertEqual(json.loads((installer.ROOT/'settings.json').read_text())['nativeBase'],'http://127.0.0.1:17841/v1')
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

    def test_router_cannot_be_its_own_native_upstream(self):
        with self.assertRaises(RuntimeError):
            installer.configure_gateway('openai_base_url = "http://127.0.0.1:17842/v1"\n')

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

    def test_stop_failure_restores_native_route_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            installer.HOME=home;installer.ROOT=home/'runtime';installer.CODEX=home/'.codex';installer.CONFIG=installer.CODEX/'config.toml'
            installer.CODEX.mkdir();installer.CONFIG.write_text('model = "native-model"\n')
            auth=home/'.commandcode/auth.json';auth.parent.mkdir();auth.write_text(json.dumps({'apiKey':'test-placeholder-only'}))
            installer.install(no_service=True)
            journal=installer.ROOT/'install.json';data=json.loads(journal.read_text());data['no_service']=False;journal.write_text(json.dumps(data))
            real_service=installer.service
            def fail_stop(*_):raise RuntimeError('Stop failed')
            installer.service=fail_stop
            try:
                with self.assertRaises(RuntimeError):installer.uninstall()
                self.assertNotIn('openai_base_url',installer.CONFIG.read_text())
                self.assertNotIn('model_provider',installer.tomllib.loads(installer.CONFIG.read_text()))
                self.assertEqual(json.loads(journal.read_text())['status'],'config_restored')
                installer.service=lambda *_:None
                installer.uninstall()
                self.assertTrue(auth.exists())
                self.assertFalse(journal.exists())
            finally:installer.service=real_service

if __name__=='__main__':
    unittest.main()
