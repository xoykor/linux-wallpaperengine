"""Single-renderer supervisor and local JSON-line control socket."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import random
import shutil
import signal
import socket
import stat
import subprocess
import time
from typing import Any

from . import model


_MAX_REQUEST = 1024 * 1024
_SCAN_SECONDS = 10.0
_CRASH_COOLDOWN = 24 * 60 * 60


class WallpaperDaemon:
    def __init__(self) -> None:
        self.config = model.load_config()
        self.catalog: dict[str, dict[str, Any]] = {}
        self.outputs: list[str] = []
        self.active = True
        self.alive = True
        self.child: subprocess.Popen | None = None
        self.child_started = 0.0
        self.child_group: int | None = None
        self.terminating = False
        self.terminate_deadline = 0.0
        self.current_id: str | None = None
        self.screens: dict[str, str] = {}
        self.pending_id: str | None = None
        self.next_change_mono: float | None = None
        self.next_change_at: float | None = None
        self.retry_at = 0.0
        self.next_scan = 0.0
        self.empty_output_scans = 0
        self.queue: list[str] = []
        self.failed: dict[str, float] = {}
        self.crash_streak = 0
        self.error: str | None = None
        self.sock: socket.socket | None = None
        self.socket_inode: int | None = None
        self._last_saved: dict[str, Any] | None = None

    def _status(self) -> dict[str, Any]:
        return {
            "running": self.active,
            "renderer_running": self.child is not None and self.child.poll() is None and not self.terminating,
            "current_id": self.current_id,
            "current_title": self.catalog.get(self.current_id or "", {}).get("title"),
            "screens": list(self.outputs),
            "screen_wallpapers": dict(self.screens),
            "outputs": list(self.outputs),
            "next_change_at": self.next_change_at,
            "pending_id": self.pending_id,
            "error": self.error,
            "catalog_count": len(self.catalog),
            "daemon_pid": os.getpid(),
            "renderer_pid": self.child.pid if self.child is not None and self.child.poll() is None else None,
            "config": self.config,
        }

    def _publish(self) -> None:
        status = self._status()
        if status != self._last_saved:
            model.save_status(dict(status, updated_at=time.time()))
            self._last_saved = status

    def _refresh(self, *, force: bool = False) -> None:
        if not force and time.monotonic() < self.next_scan:
            return
        self.next_scan = time.monotonic() + _SCAN_SECONDS
        new_catalog = model.scan_catalog()
        new_outputs = model.detect_outputs()
        if new_outputs:
            self.empty_output_scans = 0
        else:
            self.empty_output_scans += 1
            # KScreen can return no displays briefly during a Plasma login or
            # compositor restart. Keep the current renderer until the absence
            # persists across several scans.
            if self.outputs and self.empty_output_scans < 3:
                new_outputs = self.outputs
        old_outputs = self.outputs
        self.catalog = new_catalog
        self.outputs = new_outputs
        if self.child is not None and old_outputs != new_outputs:
            self._request_switch(self.current_id)
        if self.current_id and self.current_id not in new_catalog:
            self._request_switch(None)

    def _available(self, wallpaper_id: str | None, *, now: float | None = None) -> bool:
        if wallpaper_id is None or wallpaper_id not in self.catalog:
            return False
        current = time.time() if now is None else now
        return current - self.failed.get(wallpaper_id, 0) >= _CRASH_COOLDOWN

    def _eligible(self) -> list[str]:
        now = time.time()
        playlist_name = self.config["active_playlist"]
        if playlist_name is not None:
            return [
                wallpaper_id for wallpaper_id in self.config["playlists"][playlist_name]
                if self._available(wallpaper_id, now=now)
            ]
        favorites = set(self.config["favorites"])
        return [
            wallpaper_id for wallpaper_id in sorted(self.catalog, key=int)
            if (not self.config["only_favorites"] or wallpaper_id in favorites)
            and self._available(wallpaper_id, now=now)
        ]

    def _next_id(self) -> str | None:
        eligible = self._eligible()
        if not eligible:
            return None
        if not self.config["shuffle"]:
            if self.current_id in eligible:
                return eligible[(eligible.index(self.current_id) + 1) % len(eligible)]
            return eligible[0]
        self.queue = [wallpaper_id for wallpaper_id in self.queue if wallpaper_id in eligible]
        if not self.queue:
            self.queue = eligible.copy()
            random.shuffle(self.queue)
            if len(self.queue) > 1 and self.queue[0] == self.current_id:
                self.queue[0], self.queue[1] = self.queue[1], self.queue[0]
        return self.queue.pop(0)

    def _first_id(self) -> str | None:
        selected = self.config["selected_id"]
        if not self.config["rotation_enabled"] and self._available(selected):
            return selected
        eligible = self._eligible()
        if self.config["active_playlist"] is not None:
            if self.current_id in eligible:
                return self.current_id
            return self._next_id() if self.config["shuffle"] else (eligible[0] if eligible else None)
        if selected in eligible:
            return selected
        if self.current_id in eligible:
            return self.current_id
        return self._next_id()

    def _resolve_renderer(self) -> str:
        configured = self.config["renderer_path"]
        path = shutil.which("linux-wallpaperengine") if configured == "auto" else configured
        if not path and configured == "auto" and Path("/usr/bin/linux-wallpaperengine").is_file():
            path = "/usr/bin/linux-wallpaperengine"
        if not path or not Path(path).is_file() or not os.access(path, os.X_OK):
            raise RuntimeError("Renderizador linux-wallpaperengine não encontrado ou sem permissão de execução.")
        return path

    def _build_command(self, wallpaper_id: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
        if wallpaper_id not in self.catalog:
            raise ValueError("Wallpaper selecionado não está mais instalado.")
        if not self.outputs:
            raise RuntimeError("Nenhum monitor ativo detectado.")
        screens = {}
        for screen in self.outputs:
            assigned = self.config["screen_assignments"].get(screen)
            # A missing or recently crashing fixed wallpaper must not keep
            # taking down the single renderer process for every display.
            screens[screen] = assigned if self._available(assigned) else wallpaper_id

        renderer = self._resolve_renderer()
        command = [
            renderer, "--disable-mouse", "--fps", str(self.config["fps"]),
            "--layer", "bottom", "--no-fullscreen-pause",
        ]
        environment = os.environ.copy()
        if self.config["mute"]:
            command.extend(("--silent", "--noautomute", "--no-audio-processing"))
            environment.update(
                SDL_AUDIODRIVER="dummy", PULSE_SERVER="none", PIPEWIRE_REMOTE="none"
            )

        assets = [self.catalog[selected]["assets"] for selected in screens.values()]
        existing_assets = [path for path in assets if Path(path).is_dir()]
        if existing_assets:
            # The renderer has one global assets directory for all screens.
            command.extend(("--assets-dir", existing_assets[0]))

        for screen, selected in screens.items():
            command.extend((
                "--screen-root", screen,
                "--bg", self.catalog[selected]["path"],
                "--scaling", self.config["scaling"],
            ))
        return command, environment, screens

    def _request_switch(self, wallpaper_id: str | None) -> None:
        self.pending_id = wallpaper_id
        self.next_change_mono = None
        self.next_change_at = None
        if self.child is not None:
            self._signal_child(signal.SIGTERM)
            self.terminating = True
            self.terminate_deadline = time.monotonic() + 3.0

    def _signal_child(self, sig: signal.Signals | None) -> None:
        if sig is None or self.child_group is None:
            return
        try:
            os.killpg(self.child_group, sig)
        except ProcessLookupError:
            pass

    def _reap_child(self) -> None:
        if self.child is None:
            return
        returncode = self.child.poll()
        if returncode is None:
            if self.terminating and time.monotonic() >= self.terminate_deadline:
                self._signal_child(signal.SIGKILL)
                self.terminate_deadline = time.monotonic() + 1.0
            return

        was_terminating = self.terminating
        old_id = self.current_id
        active_ids = set(self.screens.values())
        if not active_ids and old_id is not None:
            active_ids.add(old_id)
        # Catch helper processes that outlived the renderer's main process.
        self._signal_child(signal.SIGKILL)
        self.child = None
        self.child_group = None
        self.terminating = False
        self.screens = {}
        self.next_change_mono = None
        self.next_change_at = None
        if not was_terminating and self.active:
            failed_at = time.time()
            for wallpaper_id in active_ids:
                self.failed[wallpaper_id] = failed_at
            if time.monotonic() - self.child_started > 60:
                self.crash_streak = 0
            self.crash_streak += 1
            delay = min(10 * 2 ** min(self.crash_streak - 1, 6), 600)
            self.error = (
                f"O renderizador encerrou (código {returncode}); "
                f"nova tentativa em {delay} segundos."
            )
            self.pending_id = self._next_id()
            self.retry_at = time.monotonic() + delay

    def _start_child(self, wallpaper_id: str) -> None:
        try:
            command, environment, screens = self._build_command(wallpaper_id)
            child = subprocess.Popen(command, start_new_session=True, env=environment)
        except (OSError, ValueError, RuntimeError) as exc:
            self.error = str(exc)
            self.retry_at = time.monotonic() + 10.0
            return
        self.child = child
        self.child_group = child.pid
        self.child_started = time.monotonic()
        self.current_id = wallpaper_id
        self.screens = screens
        self.pending_id = None
        self.error = None
        rotating = any(screen not in self.config["screen_assignments"] for screen in self.outputs)
        if self.config["rotation_enabled"] and rotating:
            interval = self.config["interval_minutes"] * 60
            self.next_change_mono = self.child_started + interval
            self.next_change_at = time.time() + interval
        else:
            self.next_change_mono = None
            self.next_change_at = None
        print(f"Wallpaper: {wallpaper_id} — {self.catalog[wallpaper_id]['title']}", flush=True)

    def _tick(self) -> None:
        self._reap_child()
        self._refresh()
        if not self.active:
            self._publish()
            return
        now = time.monotonic()
        if self.child is not None and not self.terminating and self.next_change_mono is not None:
            if now >= self.next_change_mono:
                selected = self._next_id()
                if selected and selected != self.current_id:
                    self._request_switch(selected)
                else:
                    interval = self.config["interval_minutes"] * 60
                    self.next_change_mono = now + interval
                    self.next_change_at = time.time() + interval
        if self.child is None and now >= self.retry_at:
            pending_allowed = self._available(self.pending_id)
            if self.config["rotation_enabled"]:
                pending_allowed = pending_allowed and self.pending_id in self._eligible()
            selected = self.pending_id if pending_allowed else self._first_id()
            assignments = self.config["screen_assignments"]
            if selected is None and self.outputs and all(
                screen in assignments and self._available(assignments[screen])
                for screen in self.outputs
            ):
                # All displays have fixed, currently healthy wallpapers, so no
                # separate rotation item is needed to start the renderer.
                selected = assignments[self.outputs[0]]
            if selected is None:
                if self.config["active_playlist"] is not None:
                    self.error = (
                        f"A playlist '{self.config['active_playlist']}' não contém wallpapers "
                        "instalados e disponíveis para rotação."
                    )
                elif self.catalog:
                    self.error = (
                        "Nenhum wallpaper elegível encontrado. Confira as assinaturas da Steam "
                        "e o filtro de favoritos."
                    )
                else:
                    self.error = "Nenhum wallpaper scene ou video do Workshop encontrado."
                self.retry_at = now + _SCAN_SECONDS
            elif not self.outputs:
                self.error = "Nenhum monitor ativo detectado."
                self.retry_at = now + _SCAN_SECONDS
            else:
                self._start_child(selected)
        self._publish()

    def _validate_known_id(self, value: Any) -> str:
        wallpaper_id = model.normalize_id(value)
        if wallpaper_id not in self.catalog:
            raise ValueError(f"Wallpaper {wallpaper_id} não está instalado.")
        return wallpaper_id

    def _validate_known_config(self, config: dict[str, Any]) -> None:
        """Reject newly introduced unknown IDs while tolerating stale saved ones."""
        old_favorites = set(self.config["favorites"])
        for wallpaper_id in set(config["favorites"]) - old_favorites:
            self._validate_known_id(wallpaper_id)
        selected = config["selected_id"]
        if selected is not None and selected != self.config["selected_id"]:
            self._validate_known_id(selected)
        for screen, wallpaper_id in config["screen_assignments"].items():
            if self.config["screen_assignments"].get(screen) != wallpaper_id:
                self._validate_known_id(wallpaper_id)

    def _dispatch(self, message: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(message, dict):
            raise ValueError("Solicitação inválida.")
        command = message.get("command")
        if not isinstance(command, str):
            raise ValueError("Comando inválido.")
        expected = {
            "status": set(), "next": set(), "select": {"id"},
            "set": {"settings"}, "assign": {"screen", "id"},
            "start": set(), "stop": set(), "reload": set(),
        }
        if command not in expected:
            raise ValueError(f"Comando desconhecido: {command}.")
        supplied = set(message) - {"command"}
        if supplied != expected[command]:
            raise ValueError(f"Argumentos inválidos para {command}.")

        if command == "status":
            pass
        elif command == "next":
            self._refresh(force=True)
            selected = self._next_id()
            if selected is None:
                if self.config["active_playlist"] is not None:
                    raise ValueError("A playlist ativa não contém wallpapers instalados e disponíveis.")
                raise ValueError("Nenhum wallpaper elegível para avançar.")
            self.config = model.save_config(dict(self.config, selected_id=selected))
            self.active = True
            self.retry_at = 0.0
            self._request_switch(selected)
        elif command == "select":
            self._refresh(force=True)
            selected = self._validate_known_id(message["id"])
            self.config = model.save_config(dict(
                self.config, selected_id=selected, rotation_enabled=False,
                screen_assignments={},
            ))
            self.active = True
            self.retry_at = 0.0
            self._request_switch(selected)
        elif command == "set":
            raw_settings = message["settings"]
            if not isinstance(raw_settings, dict):
                raise ValueError("As configurações devem ser um objeto.")
            settings = dict(raw_settings)
            # Turning rotation off means "keep what I am looking at". Persist
            # that choice so a daemon/session restart cannot jump back to an
            # older selected_id.
            if (
                settings.get("rotation_enabled") is False
                and self.config["rotation_enabled"]
                and "selected_id" not in settings
                and self.current_id in self.catalog
            ):
                settings["selected_id"] = self.current_id
            updated = model.validate_config(dict(self.config, **settings))
            self._refresh(force=True)
            self._validate_known_config(updated)
            previous = self.config
            self.config = model.save_config(updated)
            changed = {key for key in updated if updated[key] != previous[key]}
            if changed & {"shuffle", "favorites", "only_favorites", "playlists", "active_playlist"}:
                self.queue = []
            if self.active and changed:
                restart_keys = {"fps", "scaling", "mute", "renderer_path", "screen_assignments"}
                selected_changed = (
                    "selected_id" in changed and self.config["selected_id"] != self.current_id
                )
                if changed & restart_keys or selected_changed:
                    selected = self.config["selected_id"] if selected_changed else self.current_id
                    self.retry_at = 0.0
                    self._request_switch(selected)
                elif self.config["rotation_enabled"] and self.current_id not in self._eligible() and (
                    "rotation_enabled" in changed
                    or "active_playlist" in changed
                    or (
                        self.config["active_playlist"] is not None
                        and "playlists" in changed
                    )
                    or (
                        self.config["active_playlist"] is None
                        and changed & {"favorites", "only_favorites"}
                    )
                ):
                    selected = self._next_id()
                    self.retry_at = 0.0
                    self._request_switch(selected)
                if not self.terminating and changed & {"rotation_enabled", "interval_minutes"}:
                    rotating = any(screen not in self.config["screen_assignments"] for screen in self.outputs)
                    if self.child is not None and self.config["rotation_enabled"] and rotating:
                        interval = self.config["interval_minutes"] * 60
                        self.next_change_mono = time.monotonic() + interval
                        self.next_change_at = time.time() + interval
                    else:
                        self.next_change_mono = None
                        self.next_change_at = None
        elif command == "assign":
            self._refresh(force=True)
            screen = model.validate_screen(message["screen"])
            if screen not in self.outputs:
                raise ValueError(f"Monitor {screen} não está ativo.")
            selected = message["id"]
            assignments = dict(self.config["screen_assignments"])
            if selected is None:
                assignments.pop(screen, None)
            else:
                assignments[screen] = self._validate_known_id(selected)
            self.config = model.save_config(dict(self.config, screen_assignments=assignments))
            if self.active:
                self.retry_at = 0.0
                self._request_switch(self.current_id)
        elif command == "start":
            self.active = True
            self.retry_at = 0.0
            if self.child is None:
                self.pending_id = (
                    self._first_id() if self.config["rotation_enabled"]
                    else self.config["selected_id"] or self.current_id
                )
        elif command == "stop":
            self.active = False
            self.pending_id = None
            self.next_change_mono = None
            self.next_change_at = None
            if self.child is not None:
                self._request_switch(None)
        elif command == "reload":
            self._refresh(force=True)
            if self.active:
                selected = self.current_id if self.current_id in self.catalog else None
                self.retry_at = 0.0
                self._request_switch(selected)

        self._tick()
        return self._status()

    def _handle_client(self, client: socket.socket) -> None:
        with client:
            client.settimeout(1.0)
            try:
                with client.makefile("rb") as reader:
                    raw = reader.readline(_MAX_REQUEST + 1)
                if not raw:
                    return
                if len(raw) > _MAX_REQUEST or not raw.endswith(b"\n"):
                    raise ValueError("Solicitação grande demais ou incompleta.")
                message = json.loads(raw)
                status = self._dispatch(message)
                response = {"ok": True, "status": status}
            except (ValueError, UnicodeError, TypeError, OSError) as exc:
                response = {"ok": False, "error": str(exc)}
            try:
                client.sendall(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
            except OSError:
                pass

    def _open_socket(self) -> socket.socket:
        path = model.SOCKET_FILE
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(path))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                listener.close()
                raise
            existing = path.lstat()
            if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.getuid():
                listener.close()
                raise RuntimeError(f"Socket ocupado por outro arquivo: {path}") from exc
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.2)
                    probe.connect(str(path))
            except OSError:
                path.unlink()
                listener.bind(str(path))
            else:
                listener.close()
                raise RuntimeError("O serviço de wallpapers já está em execução.") from exc
        os.chmod(path, 0o600)
        self.socket_inode = path.stat().st_ino
        listener.listen(8)
        listener.settimeout(0.25)
        return listener

    def _close_socket(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        try:
            if model.SOCKET_FILE.lstat().st_ino == self.socket_inode:
                model.SOCKET_FILE.unlink()
        except OSError:
            pass

    def _shutdown_child(self) -> None:
        if self.child is None:
            return
        self._request_switch(None)
        deadline = time.monotonic() + 3.5
        while self.child is not None and time.monotonic() < deadline:
            self._reap_child()
            if self.child is not None:
                time.sleep(0.1)
        if self.child is not None:
            self._signal_child(signal.SIGKILL)
            try:
                self.child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            self.child = None
            self.child_group = None

    def serve(self) -> None:
        self.sock = self._open_socket()
        signal.signal(signal.SIGTERM, lambda _sig, _frame: setattr(self, "alive", False))
        signal.signal(signal.SIGINT, lambda _sig, _frame: setattr(self, "alive", False))
        signal.signal(signal.SIGUSR1, lambda _sig, _frame: self._request_switch(self._next_id()))
        try:
            self._refresh(force=True)
            while self.alive:
                self._tick()
                try:
                    client, _ = self.sock.accept()
                except socket.timeout:
                    continue
                except InterruptedError:
                    continue
                self._handle_client(client)
        finally:
            self.active = False
            self._shutdown_child()
            self._close_socket()
            self._publish()


def run() -> None:
    WallpaperDaemon().serve()
