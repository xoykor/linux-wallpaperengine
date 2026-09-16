#!/usr/bin/env python3
"""Rotate Steam Workshop wallpapers every ten minutes.

The renderer itself remains unchanged: this helper supervises one renderer
process at a time, rescans the Workshop directory before every selection, and
keeps the current wallpaper in a small state directory for manual control and
diagnostics.
"""

import json
import os
from pathlib import Path
import random
import signal
import subprocess
import time

from wallpaper_pause import PauseController


INTERVAL = 10 * 60
WORKSHOP_APP_ID = "431960"
STATE = Path.home() / ".local/state/linux-wallpaperengine"
STATE.mkdir(parents=True, exist_ok=True)

running = True
advance = False
child = None
paused = False
pause_controller = None


def stop(_signum, _frame):
    global running
    running = False


def next_wallpaper(_signum, _frame):
    global advance
    advance = True


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGUSR1, next_wallpaper)


def save(name, data):
    temp = STATE / (name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(STATE / name)


def load(name, default):
    try:
        return json.loads((STATE / name).read_text())
    except (OSError, ValueError):
        return default


def steamapps_roots():
    home = Path.home()
    data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    candidates = (
        data_home / "Steam/steamapps",
        home / ".steam/steam/steamapps",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps",
        home / "snap/steam/common/.local/share/Steam/steamapps",
    )
    roots = []
    for root in candidates:
        if root not in roots:
            roots.append(root)
    return roots


def installations():
    for steamapps in steamapps_roots():
        workshop = steamapps / "workshop/content" / WORKSHOP_APP_ID
        if workshop.is_dir():
            yield workshop, steamapps / "common/wallpaper_engine/assets"


def catalog():
    result = {}
    for workshop, assets in installations():
        for project in sorted(workshop.glob("*/project.json")):
            try:
                data = json.loads(project.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            kind = str(data.get("type", "")).lower()
            # The installed CEF web renderer is not used by this rotation
            # helper; scene and video projects are supported by the binary.
            if kind in {"scene", "video"}:
                result[project.parent.name] = {
                    "title": data.get("title", project.parent.name),
                    "type": kind,
                    "path": str(project.parent),
                    "assets": str(assets),
                }
    return result


def outputs():
    try:
        result = subprocess.run(
            ["kscreen-doctor", "-j"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return [
        output["name"]
        for output in data.get("outputs", [])
        if output.get("enabled") and output.get("connected") and output.get("name")
    ]


def terminate():
    global child, paused
    if child is None:
        return
    try:
        # A stopped renderer must resume before graceful termination.
        if paused:
            os.killpg(child.pid, signal.SIGCONT)
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
    child = None
    paused = False


def update_pause(started):
    global paused
    requested = pause_controller.poll()
    # Give a selected wallpaper two seconds to initialize before pausing it.
    desired = requested and time.monotonic() - started >= 2
    if desired == paused or child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGSTOP if desired else signal.SIGCONT)
    except ProcessLookupError:
        return
    paused = desired
    save("pause.json", {"paused": paused, "pid": child.pid, "updated_at": time.time()})
    print(
        "Wallpaper paused: maximized or fullscreen window."
        if paused
        else "Wallpaper resumed: desktop visible.",
        flush=True,
    )


previous = load("current.json", {}).get("id")
queue = load("queue.json", [])
failed = load("failed.json", {})

try:
    pause_controller = PauseController()
    pause_controller.poll()
    while running:
        items = catalog()
        now = time.time()
        eligible = [item for item in items if now - failed.get(item, 0) > 86400]
        queue = [item for item in queue if item in eligible]
        if not queue:
            queue = eligible.copy()
            random.shuffle(queue)
            if len(queue) > 1 and queue[0] == previous:
                queue[0], queue[1] = queue[1], queue[0]
        if not queue:
            print("No supported Workshop wallpapers found; retrying in 60 seconds.", flush=True)
            for _ in range(60):
                if not running:
                    break
                time.sleep(1)
            continue

        monitors = outputs()
        if not monitors:
            time.sleep(5)
            continue

        selected = queue.pop(0)
        selected_data = items[selected]
        save("queue.json", queue)
        cmd = [
            "/usr/bin/linux-wallpaperengine",
            "--silent",
            "--disable-mouse",
            "--fps",
            "30",
            "--layer",
            "bottom",
            "--no-fullscreen-pause",
        ]
        if Path(selected_data["assets"]).is_dir():
            cmd += ["--assets-dir", selected_data["assets"]]
        for monitor in monitors:
            cmd += [
                "--screen-root",
                monitor,
                "--bg",
                selected_data["path"],
                "--scaling",
                "fill",
            ]

        advance = False
        started = time.monotonic()
        child = subprocess.Popen(cmd, start_new_session=True)
        started_at = time.time()
        save(
            "pause.json",
            {"paused": False, "pid": child.pid, "updated_at": started_at},
        )
        save(
            "current.json",
            dict(
                selected_data,
                id=selected,
                pid=child.pid,
                started_at=started_at,
                interval_seconds=INTERVAL,
                next_change_at=started_at + INTERVAL,
                screens=monitors,
            ),
        )
        print(
            f"Wallpaper: {selected} — {selected_data['title']}; "
            f"next change in {INTERVAL}s",
            flush=True,
        )
        while (
            running
            and not advance
            and child.poll() is None
            and time.monotonic() - started < INTERVAL
        ):
            update_pause(started)
            time.sleep(0.2)

        crashed = child.poll() is not None
        if crashed:
            print(
                f"Wallpaper {selected} exited ({child.returncode}); skipping for 24h.",
                flush=True,
            )
            failed[selected] = time.time()
            save("failed.json", failed)
        terminate()
        previous = selected
        if crashed and running:
            time.sleep(2)
finally:
    terminate()
    if pause_controller is not None:
        pause_controller.close()
