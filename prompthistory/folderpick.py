"""Open the operating system's own folder chooser.

A browser cannot hand back a real filesystem path, so the picker runs here, on
the same machine as the server. Tkinter is in the standard library but it needs
a display and, on macOS, the main thread. Running it in a short lived
subprocess keeps a hung or missing dialog from wedging the server.
"""

from __future__ import annotations

import subprocess
import sys

_SCRIPT = """
import sys
try:
    import tkinter
    from tkinter import filedialog
except Exception as exc:
    sys.stderr.write(str(exc))
    raise SystemExit(2)

root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
chosen = filedialog.askdirectory(title="Choose a folder to sync your prompts into")
root.destroy()
sys.stdout.write(chosen or "")
"""


def pick_folder(timeout: int = 180) -> tuple[str | None, str | None]:
    """Return (folder, error). Both are None when the user simply cancelled."""
    try:
        finished = subprocess.run(
            [sys.executable, "-c", _SCRIPT],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, "The folder chooser was left open too long."
    except OSError as exc:
        return None, f"Could not open a folder chooser: {exc}"

    if finished.returncode == 2:
        return None, ("No folder chooser available on this machine. "
                      "Type the path instead.")
    if finished.returncode != 0:
        detail = (finished.stderr or "").strip().splitlines()
        return None, detail[-1] if detail else "The folder chooser failed."

    chosen = finished.stdout.strip()
    return (chosen or None), None
