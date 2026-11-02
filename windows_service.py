"""GUI-subsystem supervisor for the Windows scheduled task; no console to close."""
from pathlib import Path
import subprocess
import sys


def main():
    runtime = Path(__file__).resolve().parent
    with (runtime / 'service.log').open('a', encoding='utf-8') as log:
        child = subprocess.Popen(
            [sys.argv[1], str(runtime / 'router.cjs')],
            cwd=runtime, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return child.wait()


if __name__ == '__main__':
    sys.exit(main())
