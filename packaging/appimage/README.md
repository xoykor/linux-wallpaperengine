# AppImage

The AppImage puts the desktop frontend, C++ wallpaper engine, and CEF runtime in one downloadable file. It uses the existing Steam Wallpaper Engine installation for the subscribed wallpapers and the Steam asset directory.

## Runtime requirements

The AppImage includes the project binaries and Python frontend. The host still needs Python 3, PyGObject, GTK 4 and GdkPixbuf introspection, GTK 3/NSS for CEF, OpenGL drivers, and the shared system libraries used by the engine (including SDL2, FFmpeg, mpv, PulseAudio, Wayland/X11, ALSA, CUPS, and FreeType). The AppRun checks the Python/GTK requirements and gives an error if they are missing. Opening the AppImage starts its user daemon for that GUI session; closing the window stops a daemon started by that AppImage. Session autostart remains disabled in AppImage mode. The AppImage does not include Steam or wallpaper downloads.

## Build

Configure and build the CMake target first, then install `appimagetool` 1.9.1 or set `APPIMAGETOOL` to its executable:

```sh
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 2
packaging/appimage/build-appimage.sh \
  --build-dir build \
  --output-dir outputs \
  --version v0.0.4
```

The script creates an AppDir, installs the engine and its CEF files there, strips CEF debug sections to keep the download smaller, and writes a SHA-256 file next to the AppImage. It does not install files into the host system.

Pushing a `v*` tag runs the release workflow and attaches the AppImage and checksum to the GitHub release. A manual workflow run builds an artifact without publishing a release.
