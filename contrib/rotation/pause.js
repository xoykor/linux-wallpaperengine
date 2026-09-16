// KWin supplies the actual window state, including native Wayland windows.
const tracked = new Set();
let lastCovered = null;

function coversDesktop(w) {
    if (w.deleted || w.specialWindow || w.minimized || w.hidden) return false;
    const desktop = workspace.currentDesktopForScreen(w.output);
    if (!w.onAllDesktops && !w.desktops.includes(desktop)) return false;
    if (w.activities.length && !w.activities.includes(workspace.currentActivity)) return false;
    return w.fullScreen || Number(w.maximizeMode) === 3;
}

function updateCoverage() {
    const covered = Array.from(tracked).some(coversDesktop);
    if (covered === lastCovered) return;
    lastCovered = covered;
    callDBus("io.local.WallpaperRotation", "/Pause", "io.local.WallpaperRotation",
        "SetCovered", covered);
}

function watchWindow(w) {
    if (tracked.has(w)) return;
    tracked.add(w);
    [w.maximizedChanged, w.fullScreenChanged, w.minimizedChanged,
        w.hiddenChanged, w.desktopsChanged, w.activitiesChanged,
        w.outputChanged].forEach(function (event) { event.connect(updateCoverage); });
    updateCoverage();
}

workspace.windowAdded.connect(watchWindow);
workspace.windowRemoved.connect(function (w) {
    tracked.delete(w);
    updateCoverage();
});
workspace.currentDesktopChanged.connect(updateCoverage);
workspace.currentActivityChanged.connect(updateCoverage);
workspace.screensChanged.connect(updateCoverage);
workspace.windowList().forEach(watchWindow);
updateCoverage();
