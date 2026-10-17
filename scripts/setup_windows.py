"""Install local browser dependencies into this checkout's virtual environment."""
from pathlib import Path
import os
import subprocess
import sys
import venv


def main():
    if os.name != 'nt':
        raise SystemExit('This installer is for Windows. See docs/windows-browser.md for other systems.')
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11 or newer is required. Install Python and run this again.')
    root = Path(__file__).resolve().parents[1]
    environment = root / '.venv'
    if environment.exists() and not (environment / 'pyvenv.cfg').is_file():
        raise SystemExit('.venv already exists but is not a virtual environment; preserve it and check the path.')
    if not environment.exists():
        venv.create(environment, with_pip=True)
    python = environment / 'Scripts/python.exe'
    subprocess.run([str(python), '-m', 'pip', 'install', '-e', '.[browser]'], cwd=root, check=True)
    subprocess.run([str(python), '-m', 'playwright', 'install', 'chromium'], cwd=root, check=True)
    subprocess.run([str(python), '-m', 'homepan.browser', 'status'], cwd=root, check=True)
    print('Setup complete. Run scripts\\login-windows.cmd to log in directly.')


if __name__ == '__main__':
    main()
