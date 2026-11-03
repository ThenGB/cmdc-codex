"""The router must run without a console and report its real exit status."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'win32', 'Windows console isolation')
class WindowsLauncherTests(unittest.TestCase):
    def test_child_has_no_console_and_exit_status_is_preserved(self):
        launcher = Path(__file__).with_name('windows_service.py')
        self.assertTrue(launcher.is_file(), 'The task needs a console-free supervisor')
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        self.assertTrue(pythonw.is_file())
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            shutil.copyfile(launcher, runtime / launcher.name)
            # A small executable child observes the Windows state itself.
            (runtime / 'router.cjs').write_text(
                'import ctypes,json,sys\n'
                'print(json.dumps({"console":ctypes.windll.kernel32.GetConsoleWindow()}),flush=True)\n'
                'sys.exit(23)\n', encoding='utf-8')
            result = subprocess.run([str(pythonw), str(runtime / launcher.name), sys.executable],
                                    stdin=subprocess.DEVNULL, timeout=10)
            self.assertEqual(result.returncode, 23)
            self.assertEqual(json.loads((runtime / 'service.log').read_text())['console'], 0)


if __name__ == '__main__':
    unittest.main()
