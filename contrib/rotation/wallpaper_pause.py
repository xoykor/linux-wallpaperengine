"""Bridge KDE window events to the Wallpaper Engine rotation process."""

import time
from pathlib import Path

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


INTERFACE = "io.local.WallpaperRotation"
PLUGIN = "wallpaper-rotation-pause"
SCRIPT = Path.home() / ".local/share/linux-wallpaperengine/pause.js"


class PauseController(dbus.service.Object):
    def __init__(self):
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        self.bus_name = dbus.service.BusName(INTERFACE, self.bus, do_not_queue=True)
        super().__init__(self.bus_name, "/Pause")
        self.covered = False
        self.showing_desktop = False
        self.needs_load = True
        self.retry_at = 0
        self.context = GLib.MainContext.default()
        self.bus.add_signal_receiver(
            self.owner_changed,
            signal_name="NameOwnerChanged",
            arg0="org.kde.KWin",
            dbus_interface="org.freedesktop.DBus",
        )
        self.bus.add_signal_receiver(
            self.desktop_changed,
            signal_name="showingDesktopChanged",
            dbus_interface="org.kde.KWin",
            bus_name="org.kde.KWin",
            path="/KWin",
        )

    @dbus.service.method(INTERFACE, in_signature="b", out_signature="")
    def SetCovered(self, covered):
        self.covered = bool(covered)

    def desktop_changed(self, showing):
        self.showing_desktop = bool(showing)

    def owner_changed(self, _name, _old, _new):
        self.covered = False
        self.needs_load = True
        self.retry_at = 0

    def scripting(self):
        return dbus.Interface(
            self.bus.get_object("org.kde.KWin", "/Scripting"),
            "org.kde.kwin.Scripting",
        )

    def load_script(self):
        scripting = self.scripting()
        scripting.unloadScript(PLUGIN)
        script_id = scripting.loadScript(str(SCRIPT), PLUGIN, signature="ss")
        if script_id < 0:
            raise RuntimeError("KWin could not load fullscreen-window detection")
        props = dbus.Interface(
            self.bus.get_object("org.kde.KWin", "/KWin"),
            "org.freedesktop.DBus.Properties",
        )
        self.showing_desktop = bool(props.Get("org.kde.KWin", "showingDesktop"))
        script = self.bus.get_object("org.kde.KWin", f"/Scripting/Script{script_id}")
        dbus.Interface(script, "org.kde.kwin.Script").run()
        self.needs_load = False
        print("KDE automatic pause connected.", flush=True)

    def poll(self):
        while self.context.pending():
            self.context.iteration(False)
        if self.needs_load and time.monotonic() >= self.retry_at:
            try:
                self.load_script()
            except (dbus.DBusException, RuntimeError) as exc:
                self.retry_at = time.monotonic() + 5
                print(f"Waiting for KDE window detection: {exc}", flush=True)
        return self.covered and not self.showing_desktop

    def close(self):
        try:
            self.scripting().unloadScript(PLUGIN)
        except dbus.DBusException:
            pass
        self.remove_from_connection()
