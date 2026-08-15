"""Optional Windows notification-area controls for the local web application."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable
import webbrowser


def start_windows_tray(url: str, icon_path: Path, on_exit: Callable[[], None],
                       *, backend=None, image_open=None):
    """Start a Windows tray icon, returning it so the caller can stop it on shutdown.

    Imports stay inside the Windows branch. Linux installations therefore neither load
    nor package the tray backend, and a missing optional Windows backend never prevents
    the local server from starting.
    """
    if os.name != "nt":
        return None
    if backend is None:
        import pystray as backend
    if image_open is None:
        from PIL import Image
        image_open = Image.open

    def open_app(_icon=None, _item=None) -> None:
        webbrowser.open(url, new=1)

    def exit_app(icon, _item=None) -> None:
        icon.stop()
        on_exit()

    menu = backend.Menu(
        backend.MenuItem("Open Civitai Artist Discovery", open_app, default=True),
        backend.MenuItem("Exit", exit_app),
    )
    icon = backend.Icon("CivitaiArtistDiscovery", image_open(icon_path),
                        "Civitai Artist Discovery", menu)
    icon.run_detached()
    return icon


def stop_windows_tray(icon) -> None:
    if icon is not None:
        icon.stop()
