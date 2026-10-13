"""Check the player runtime before starting or patching the game."""
import sys
from pathlib import Path


def main():
    if sys.version_info < (3, 10):
        raise SystemExit('Python 3.10 or newer is required; use the Windows player ZIP.')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.play_hulk import bundled_tk_environment
    bundled_tk_environment()
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
    except Exception as error:
        raise SystemExit('Tk could not start: ' + str(error))
    from localserver.runtime_content import require_runtime_content
    require_runtime_content(Path(__file__).resolve().parents[1])
    print('Runtime OK: Python ' + sys.version.split()[0] + ', Tk ' + str(tk.TkVersion))


if __name__ == '__main__':
    main()
