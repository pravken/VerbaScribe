"""
Native OS file and folder picker executed in an isolated subprocess.
Running in a separate process avoids all thread-affinity and deadlock issues
with the ASGI (uvicorn) worker threads and pywebview's GUI message pump.
"""

import argparse
import sys
from pathlib import Path


def pick_save(
    initial_dir: str = "",
    default_name: str = "",
    file_filter: str = "",
    default_ext: str = "",
    title: str = "Сохранить как",
) -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    try:
        filetypes = []
        if file_filter:
            parts = [p.strip() for p in file_filter.split("|") if p.strip()]
            for i in range(0, len(parts) - 1, 2):
                filetypes.append((parts[i], parts[i + 1]))
        if not filetypes:
            filetypes = [("All files", "*.*")]

        res = filedialog.asksaveasfilename(
            parent=root,
            title=title or "Сохранить как",
            initialdir=initial_dir or None,
            initialfile=default_name or None,
            defaultextension=default_ext or None,
            filetypes=filetypes,
        )
        return str(res or "")
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def pick_folder(initial_dir: str = "", title: str = "Выбор папки") -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    try:
        res = filedialog.askdirectory(
            parent=root,
            title=title or "Выбор папки",
            initialdir=initial_dir or None,
        )
        return str(res or "")
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="VerbaScribe Native File Dialog")
    parser.add_argument("--mode", choices=["save", "folder"], required=True)
    parser.add_argument("--dir", default="")
    parser.add_argument("--filename", default="")
    parser.add_argument("--filter", default="")
    parser.add_argument("--ext", default="")
    parser.add_argument("--title", default="")

    args = parser.parse_args()
    if args.mode == "save":
        result = pick_save(
            initial_dir=args.dir,
            default_name=args.filename,
            file_filter=args.filter,
            default_ext=args.ext,
            title=args.title,
        )
    else:
        result = pick_folder(initial_dir=args.dir, title=args.title)

    if result:
        sys.stdout.write(result)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
