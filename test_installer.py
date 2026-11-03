import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer',Path(__file__).with_name('cmdc-codex.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.validate_patch=patch.object(installer,'validate_commandcode_key')
        self.validate=self.validate_patch.start()
        self.addCleanup(self.validate_patch.stop)

    def test_detects_commandcode_cli_login(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            installer.HOME=home
            auth=home/'.commandcode/auth.json'
            auth.parent.mkdir()
            auth.write_text(json.dumps({'apiKey':'detected-placeholder-only','userName':'test'}))
            self.assertEqual(installer.detect_commandcode_key(),('detected-placeholder-only',auth))

    def test_detects_platform_app_data_login(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);installer.HOME=home
            environment={'APPDATA':str(home/'appdata'),'LOCALAPPDATA':str(home/'local'),
                         'XDG_CONFIG_HOME':str(home/'xdg')}
            with patch.dict(installer.os.environ,environment,clear=False):
                if installer.sys.platform=='win32':
                    auth=home/'appdata/commandcode/auth.json'
                elif installer.sys.platform=='darwin':
                    auth=home/'Library/Application Support/commandcode/auth.json'
                else:
                    auth=home/'xdg/commandcode/auth.json'
                auth.parent.mkdir(parents=True);auth.write_text(json.dumps({'apiKey':'platform-placeholder-only'}))
                self.assertEqual(installer.detect_commandcode_key(),('platform-placeholder-only',auth))

    def test_missing_commandcode_login_prompts_and_stores_key(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            installer.HOME=home;installer.ROOT=home/'runtime';installer.CODEX=home/'.codex';installer.CONFIG=installer.CODEX/'config.toml'
            installer.CODEX.mkdir();installer.CONFIG.write_text('model = "native-model"\n')
            with patch.object(installer.getpass,'getpass',return_value='prompted-placeholder-only'):
                installer.install(no_service=True)
            self.validate.assert_called_once_with('prompted-placeholder-only')
            self.assertEqual(json.loads((installer.ROOT/'credentials.json').read_text())['apiKey'],'prompted-placeholder-only')
            self.assertTrue(json.loads((installer.ROOT/'install.json').read_text())['owns_key'])

    def test_rejected_login_prompts_for_replacement_key(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            installer.HOME=home;installer.ROOT=home/'runtime';installer.CODEX=home/'.codex';installer.CONFIG=installer.CODEX/'config.toml'
            installer.CODEX.mkdir();installer.CONFIG.write_text('model = "native-model"\n')
            auth=home/'.commandcode/auth.json';auth.parent.mkdir()
            auth.write_text(json.dumps({'apiKey':'stale-placeholder-only'}))
            self.validate.side_effect=[RuntimeError('CommandCode login/API key was rejected (HTTP 401)'),None]
            with patch.object(installer.getpass,'getpass',return_value='replacement-placeholder-only'):
                installer.install(no_service=True)
            self.assertEqual([call.args[0] for call in self.validate.call_args_list],
                             ['stale-placeholder-only','replacement-placeholder-only'])
            self.assertEqual(json.loads((installer.ROOT/'credentials.json').read_text())['apiKey'],
                             'replacement-placeholder-only')

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

    def test_existing_bili_wrapper_is_preserved_and_used_for_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            installer.HOME=home;installer.ROOT=home/'runtime';installer.CODEX=home/'.codex';installer.CONFIG=installer.CODEX/'config.toml'
            installer.CODEX.mkdir()
            native_wrapper='http://127.0.0.1:8787/bili/https://chatgpt.com/backend-api/codex'
            original=(f'openai_base_url = "{native_wrapper}"\nmodel = "native-model"\n'
                      '[marketplaces.ponytail]\nsource_type = "git"\n'
                      '[plugins."ponytail@ponytail"]\nenabled = true\n')
            installer.CONFIG.write_text(original)
            auth=home/'.commandcode/auth.json';auth.parent.mkdir();auth.write_text(json.dumps({'apiKey':'test-placeholder-only'}))
            installer.install(no_service=True)
            installed=installer.CONFIG.read_text()
            effective=installer.tomllib.loads(installed)
            wrapped_router='http://127.0.0.1:8787/bili/http://127.0.0.1:17842/v1'
            self.assertEqual(effective['openai_base_url'],wrapped_router)
            self.assertEqual(effective['model_providers']['cmdc-codex']['base_url'],wrapped_router)
            self.assertEqual(effective['plugins']['ponytail@ponytail']['enabled'],True)
            self.assertEqual(effective['marketplaces']['ponytail']['source_type'],'git')
            self.assertEqual(json.loads((installer.ROOT/'settings.json').read_text())['nativeBase'],native_wrapper)
            installer.uninstall()
            restored=installer.tomllib.loads(installer.CONFIG.read_text())
            self.assertEqual(restored['openai_base_url'],native_wrapper)
            self.assertEqual(restored['plugins']['ponytail@ponytail']['enabled'],True)
            self.assertNotIn('cmdc-codex',restored.get('model_providers',{}))

    def test_active_plugin_wrapper_survives_upgrade_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            installer.HOME=home;installer.ROOT=home/'runtime';installer.CODEX=home/'.codex';installer.CONFIG=installer.CODEX/'config.toml'
            installer.CODEX.mkdir();installer.CONFIG.write_text('model = "native-model"\n')
            auth=home/'.commandcode/auth.json';auth.parent.mkdir();auth.write_text(json.dumps({'apiKey':'test-placeholder-only'}))
            installer.install(no_service=True)
            wrapped=installer.CONFIG.read_text().replace(
                'http://127.0.0.1:17842/v1','http://127.0.0.1:8787/bili/http://127.0.0.1:17842/v1')
            wrapped+='\n[marketplaces.ponytail]\nsource = "https://github.com/DietrichGebert/ponytail.git"\n'
            wrapped+='\n[plugins."ponytail@ponytail"]\nenabled = true\n'
            installer.CONFIG.write_text(wrapped)
            text,changes=installer.configure_gateway(wrapped)
            self.assertEqual(text,wrapped)
            self.assertEqual(changes,[])
            installer.upgrade()
            self.assertEqual(installer.CONFIG.read_text(),wrapped)
            journal=json.loads((installer.ROOT/'install.json').read_text())
            self.assertEqual(journal['version'],installer.VERSION)
            self.assertEqual(json.loads((installer.ROOT/'settings.json').read_text())['version'],installer.VERSION)

    def test_upgrade_preserves_supervisor_interpreter(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            installer.HOME=home;installer.ROOT=home/'runtime';installer.CODEX=home/'.codex';installer.CONFIG=installer.CODEX/'config.toml'
            installer.CODEX.mkdir();installer.CONFIG.write_text('model = "native-model"\n')
            auth=home/'.commandcode/auth.json';auth.parent.mkdir();auth.write_text(json.dumps({'apiKey':'test-placeholder-only'}))
            installer.install(no_service=True)
            journal_path=installer.ROOT/'install.json'
            journal=json.loads(journal_path.read_text())
            journal['pythonw']=str(home/'existing-pythonw.exe')
            installer.write_json(journal_path,journal)
            before=installer.CONFIG.read_text()
            installer.upgrade()
            upgraded=json.loads(journal_path.read_text())
            self.assertEqual(upgraded['pythonw'],journal['pythonw'])
            self.assertEqual(installer.CONFIG.read_text(),before)

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

class CommandCodeValidationTests(unittest.TestCase):
    def test_provider_key_validation_uses_provider_scope_and_client_headers(self):
        class Response(io.BytesIO):
            status=200
            def __enter__(self):return self
            def __exit__(self,*_):pass
        captured={}
        def urlopen(request,timeout):
            captured.update(url=request.full_url,timeout=timeout,
                            authorization=request.get_header('Authorization'),
                            user_agent=request.get_header('User-agent'),
                            accept=request.get_header('Accept'))
            return Response(b'{"data":[]}')
        with patch.object(installer.urllib.request,'urlopen',side_effect=urlopen):
            installer.validate_commandcode_key('placeholder-only')
        self.assertEqual(captured['url'],installer.COMMANDCODE_API+'/provider/v1/models?view=client')
        self.assertEqual(captured['authorization'],'Bearer placeholder-only')
        self.assertEqual(captured['user_agent'],'cmdc-codex/'+installer.VERSION)
        self.assertEqual(captured['accept'],'application/json')
        self.assertEqual(captured['timeout'],20)

if __name__=='__main__':
    unittest.main()
