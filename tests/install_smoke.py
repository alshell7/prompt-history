"""Verify a built wheel from outside the checkout in a fresh Python environment.

Run after `python -m build`: python tests/install_smoke.py
"""

import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import venv
from pathlib import Path


def main():
    wheel = next((Path(__file__).resolve().parents[1] / "dist").glob("*.whl"))
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PROMPT_HISTORY_SYNC_ENABLED"] = "false"
    with tempfile.TemporaryDirectory(prefix="prompt-history-install-") as folder:
        root = Path(folder)
        venv.EnvBuilder(with_pip=True).create(root / "env")
        scripts = root / "env" / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
                       cwd=root, env=env, check=True)
        version = wheel.name.split("-")[1]
        for command in ("prompt-history", "phist"):
            entry = scripts / (command + (".exe" if os.name == "nt" else ""))
            output = subprocess.check_output([str(entry), "--version"], cwd=root, env=env, text=True)
            assert version in output, output
        # Use a configurable OS-assigned port and exercise the bundled HTML.
        with (root / "server.log").open("w+") as log:
            process = subprocess.Popen([str(python), "-m", "prompthistory", "--port", "0", "--no-browser"],
                                       cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 20
                match = None
                while time.monotonic() < deadline:
                    log.seek(0)
                    output = log.read()
                    match = re.search(r"http://127\.0\.0\.1:(\d+)/", output)
                    if match or process.poll() is not None:
                        break
                    time.sleep(0.1)
                assert match, output
                assert 0 < int(match[1]) <= 65535
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(match[0], timeout=10) as response:
                    html = response.read().decode("utf-8")
                assert "Prompt History" in html and "provider-icon" in html and "renderPrompt" in html
            finally:
                process.terminate()
                process.wait(timeout=10)
        print(f"Installed {version}: both global CLI commands and configurable-port server work outside the checkout.")


if __name__ == "__main__":
    main()
