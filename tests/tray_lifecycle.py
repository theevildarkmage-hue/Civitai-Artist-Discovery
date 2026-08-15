"""The Windows tray exposes Open and Exit without becoming a Linux dependency."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discovery.tray as tray


class Item:
    def __init__(self, text, action, default=False):
        self.text, self.action, self.default = text, action, default


class Menu(tuple):
    def __new__(cls, *items):
        return tuple.__new__(cls, items)


class Icon:
    def __init__(self, name, image, title, menu):
        self.name, self.image, self.title, self.menu = name, image, title, menu
        self.started = self.stopped = False

    def run_detached(self):
        self.started = True

    def stop(self):
        self.stopped = True


class Backend:
    Menu = Menu
    MenuItem = Item
    Icon = Icon


if tray.os.name == "nt":
    opened = []
    exited = []
    original_open = tray.webbrowser.open
    tray.webbrowser.open = lambda url, new=0: opened.append((url, new))
    try:
        icon = tray.start_windows_tray("http://127.0.0.1:1234", Path("app.ico"),
                                       lambda: exited.append(True), backend=Backend,
                                       image_open=lambda path: ("image", path))
        assert icon.started and icon.title == "Civitai Artist Discovery"
        assert icon.menu[0].default and icon.menu[0].text.startswith("Open")
        icon.menu[0].action(icon, icon.menu[0])
        assert opened == [("http://127.0.0.1:1234", 1)], opened
        icon.menu[1].action(icon, icon.menu[1])
        assert icon.stopped and exited == [True]
    finally:
        tray.webbrowser.open = original_open
else:
    assert tray.start_windows_tray("http://127.0.0.1:1234", Path("app.ico"),
                                   lambda: None, backend=Backend,
                                   image_open=lambda path: path) is None

print({"windowsOnly": True, "openAction": True, "exitAction": True,
       "detachedLifecycle": True})
