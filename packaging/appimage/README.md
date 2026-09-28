# AppImage

The AppImage puts the desktop frontend, C++ wallpaper engine, and CEF runtime in one downloadable file. It uses the existing Steam Wallpaper Engine installation for the subscribed wallpapers and the Steam asset directory.

## Runtime requirements

The AppImage includes the project binaries, Python frontend, and shared libraries needed by the renderer. The host still needs Python 3, PyGObject, GTK 4 and GdkPixbuf introspection, GTK 3/NSS for CEF, OpenGL drivers, and platform libraries such as Wayland/X11, ALSA, CUPS, and FreeType. The AppRun checks the Python/GTK requirements and gives an error if they are missing. Opening the AppImage starts a separate AppImage instance for its user daemon; closing the control window leaves the wallpaper running and keeps the daemon's bundled engine files mounted. The **Stop** button stops playback. Reopening the same version reconnects to that daemon; opening a newer version retires the older daemon and starts the new one. Session autostart remains disabled in AppImage mode. The AppImage does not include Steam or wallpaper downloads.

The bundled renderer takes precedence over paths saved by an earlier system installation. On KDE X11/XWayland, the window requests native compositor backdrop blur; other compositors retain the tinted dark surface without the compositor blur effect.

## Build

Configure and build the CMake target first, then install `appimagetool` 1.9.1 and `linuxdeploy`, or pass their executable paths:

```sh
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 2
packaging/appimage/build-appimage.sh \
  --build-dir build \
  --output-dir outputs \
  --version 0.0.18 \
  --appimagetool /path/to/appimagetool.AppImage \
  --linuxdeploy /path/to/linuxdeploy.AppImage
```

The script creates an AppDir, installs the engine and its CEF files, deploys the engine's shared-library dependencies, strips CEF debug sections, and writes a portable SHA-256 file next to the AppImage. It removes the temporary AppDir after packaging. It does not install files into the host system.

Pushing a `v*` tag runs the release workflow and attaches the AppImage and checksum to the GitHub release. A manual workflow run builds an artifact without publishing a release.
