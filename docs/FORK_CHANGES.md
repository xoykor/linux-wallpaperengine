# Fork changes and upstream proposal

This document records the complete net change in `xoykor/linux-wallpaperengine`
relative to `Almamu/linux-wallpaperengine` at upstream `main` commit
`b016d7d` (2026-09-27), plus the renderer fixes being prepared on the proposed
upstream branch. It is intended to let upstream reviewers assess the fork as a
whole without relying on screenshots or commit history alone.

The comparison is a three-dot diff from the common upstream ancestor. Fork
`main` has 357 commits beyond that ancestor; the proposed branch adds one
commit, for 358 commits total and a 95-path net diff. The existing fork delta
covers 93 paths. This proposal adds this document and the SceneScript adapter
header, modifies ten renderer and scripting paths, and updates frontend layout
and appearance. The fork history includes imported and merged upstream work,
so the net file diff is the more useful measure of its scope.

## Overview

The fork adds four connected areas of work:

1. A GTK 4 desktop application for finding, previewing and controlling local
   Steam Workshop wallpapers, with favorites, playlists, per-display pins,
   language and appearance settings.
2. Linux renderer and SceneScript compatibility work, including multi-display
   startup, camera and image composition handling, Workshop parsing, skeletal
   models, video playback and shader compatibility.
3. A bundled AppImage path containing both the GTK application and the C++
   renderer, with runtime selection that prefers the bundled engine.
4. Documentation, installation helpers, CI checks and a playlist rotation
   utility.

The GTK application does not download wallpapers or authenticate to Steam. It
reads the Steam installation and Workshop files already present on the machine.
The AppImage includes the renderer and frontend, but intentionally uses the
user's Steam Workshop content and host graphics/runtime libraries.

## Desktop application

### Library and previews

`app/wallpaper_engine_app/gui.py` implements the GTK 4 window and its Library,
Playlists, Languages and Settings pages. The Library supports search, type and
favorite filters, incremental card creation, selection of individual or
multiple wallpapers, range selection, and selected-wallpaper details. Applying
a wallpaper starts it on every display, stops a running playlist and clears
per-display pins. The details panel can mark or unmark favorites and apply a
wallpaper to all screens or a selected screen.

`app/wallpaper_engine_app/previews.py` reads image previews from each wallpaper's
own Workshop directory. It handles static image formats and animated GIFs,
decodes frames in background workers, scales frames into bounded preview sizes,
caches decoded results, and starts GIF animation on card hover. The catalog
model records both the preview poster and any animation source so a wallpaper
without a GIF does not block the card list.

Library cards use a compact fixed width with a uniform 16:9 preview region.
Images keep their original proportions inside that region, so non-widescreen
previews are letterboxed instead of cropped. GIFs use their first frame at rest
and decode/play their animation asynchronously while the pointer hovers over a
card. The gallery spacing is intentionally tight so maximized windows show more
wallpapers per row. Visual behavior still depends on GTK's FlowBox allocation
and should be checked on the target desktop.

### Catalog, state and engine control

`model.py` locates Steam roots, parses library-folder metadata, scans the local
Workshop catalog, extracts project tags and properties, resolves preview files,
and validates/saves user settings. The catalog refresh path detects files added
by Steam while the app is open. Favorites, playlists, monitor selection,
language and other application preferences are stored under XDG user data
locations rather than in the repository or system configuration.

`daemon.py` owns the renderer process and playlist rotation. It resolves the
renderer path, starts and stops the engine, enforces per-screen wallpaper
assignments, advances playlists on a configurable interval, honors favorite
and shuffle options, and reports renderer status to the GUI. `ipc.py` defines
the local command channel; `__main__.py` starts the GUI/daemon entry points.
The desktop systemd service keeps the renderer running after the control
window closes. AppImage mode uses a session daemon and avoids enabling a
system-wide or login autostart service implicitly.

### Appearance, localization and input

`theme.py` supplies the GTK styles and user-selectable accent palette.
`backdrop.py` requests KWin backdrop blur after the GTK window is mapped on
KDE X11/XWayland. The window uses a darker, higher-opacity plum/charcoal tint
with subtle violet and warm color shifts, retaining contrast while letting
the blurred background show through the glass. Other compositors may ignore
the KWin hint, so blur is not guaranteed outside KDE X11/XWayland; those
sessions still get the tinted surface. The library window has responsive page and navigation layouts,
keyboard shortcuts, localized labels, status feedback and per-monitor
controls.

`i18n.py` provides the language catalog and system-language fallback. The UI
offers English, Portuguese, German, Russian, Japanese, Mandarin Chinese,
Spanish and Hindi, and preserves an existing user's choice.

## Renderer and Linux engine changes

### Workshop discovery and data parsing

The engine now searches common native, Flatpak and Snap Steam locations,
additional Steam libraries listed by `libraryfolders.vdf`, and user-selected
asset paths. `src/Steam/FileSystem/FileSystem.cpp` and its header provide the
filesystem/library resolution changes.

The data model and parsers were extended for additional Wallpaper Engine
project and Workshop forms. This covers object variants and properties,
material and texture metadata, wallpaper settings, JSON compatibility,
object groups/parents, camera assets and project data that was previously
discarded or interpreted with the wrong type. The main implementation is in
`src/WallpaperEngine/Data/Model/` and `src/WallpaperEngine/Data/Parsers/`.

### Scene composition, transforms and camera

The renderer adds scene camera objects, perspective and orthographic camera
paths, camera script transforms and mouse-coordinate conversion for X11 and
Wayland. Scene composition layers are rendered through their own framebuffer
paths, with parent/child ordering, visibility, fill/fit/stretch handling,
post-processing and output viewport mapping accounted for in scene rendering.
The pending CModel change composes a model's current local transform with its
complete authored parent chain, from the root to the rendered model, and uses
script-updated values each frame. This is intended to fix nested models and
other parented assets generically instead of adjusting individual wallpapers.

The camera code uses internal radians for GLM transforms and bridges
SceneScript layer angles through degrees at the JS API boundary. The script API
and renderer therefore retain their respective units without applying a
wallpaper-specific angle exception.

### Puppet and skeletal-model compatibility

`CModel` and `PuppetModel` add support for skinned MDLV models, bone hierarchy,
mesh weights, attachments, animation clips, animation layers and skinning.
The parser handles the MDLS0004 layout and valid helper bones, checks bone
indices against mesh weights, supports animation chunks following either
MDLA- or MDAT-pointed skeleton data, and tolerates variable record padding.
Invalid or absent skeleton data degrades to bind-pose rendering instead of
preventing unrelated scene assets from loading.

The parser accepts valid helper bones and animation records without rejecting
an otherwise usable rig. The pending model changes also expose named puppet
animation layers to SceneScript through a common `ScriptableObject` contract
(`getAnimationLayer(name)`, `rate`, `play()` and `stop()`). Both image and model
objects provide the lookup, so scripts can control puppet animation without
wallpaper-specific branches.

### SceneScript runtime and render behavior

The scripting changes expand SceneScript built-ins, vector/math/color adapters,
layer property access, input values, camera transforms, script property parsing
and per-frame updates. The pending ScriptEngine change treats an absent
`update()` function as an init-only script and leaves a property's current
value intact when an existing `update()` has side effects and returns
`undefined`. Explicit `null` remains distinct. This is needed for scripts that
write another layer's property from a hook without returning a replacement for
the property to which the hook is attached.

### Video, texture, shader and output support

The fork changes MPV OpenGL playback, frame handling, synchronization and audio
options. Web/video wallpaper selection and texture upload paths are adjusted
for renderer lifecycle and memory ownership. Shader translation adds
compatibility handling for Workshop GLSL that relies on varying widths,
uniform-dependent constants, fragment texture coordinates or vector operand
widths that differ from desktop GLSL conventions. Post-process state and
framebuffer providers carry more of the project’s scene settings through the
render passes.

Linux display/input changes cover GLFW and Wayland mouse state, output viewports,
screen roots/spans and monitor geometry. They are intended to match the
renderer’s per-screen surface and SceneScript coordinate spaces.

## AppImage, install and release flow

`packaging/appimage/build-appimage.sh` stages the GTK Python package, C++
renderer, CEF files, desktop entry and icon in one AppDir. It strips CEF debug
sections, runs pinned `linuxdeploy` to collect the renderer's shared-library
dependencies, checks the resulting runtime search path, then runs
`appimagetool` and writes a portable SHA-256 sidecar. Temporary AppDir files
are removed when packaging ends. It does not install the engine into the host
filesystem. `packaging/appimage/AppRun` selects the bundled renderer first,
then starts the desktop frontend; bundled library paths are applied only to
the renderer child so the host GTK Python bindings keep using their matching
system libraries. Steam and downloaded wallpaper content stay on the host.

`.github/workflows/appimage.yml` builds AppImages for `v*` tags and uploads
the image and checksum to GitHub Releases. `workflow_dispatch` builds an
artifact without publishing a release. The current release process is x86_64.
The AppImage bundles the project executable, CEF runtime, and renderer shared
libraries such as GLEW, FFmpeg, mpv, and KissFFT. GTK/Python introspection,
graphics drivers, and platform libraries still need to be available on the
system. The exact host requirements are listed in
[`packaging/appimage/README.md`](../packaging/appimage/README.md).

`app/install.sh` installs the optional desktop control app for the current
user, creates its desktop entry and user service, and can remove those user
files. It is separate from packaging the AppImage and does not install or
replace the engine using a system package manager. `contrib/rotation/` contains
an independent playlist rotation service/helper for users who do not use the
GTK control app.

## CI and regression coverage

`.github/workflows/cmake.yml` adds Python syntax checks, installer shell
syntax, desktop-app regression tests, C++ formatting, unit-test and build
jobs. `.github/workflows/tests.yml` preserves a manually runnable engine test
job. `app/tests/test_regressions.py` covers selected application regressions,
including daemon state and catalog/config behavior. CI workflow details and
runtime dependencies should be reviewed independently from the renderer
changes because they affect different deployment paths.

## Generic renderer corrections and validation

The active renderer changes address classes of scene data and script behavior
rather than embedding rules for particular Workshop items:

- Model transforms are composed through the authored parent chain using the
  current per-frame property values, so animation and script changes on an
  ancestor carry through to nested models.
- MDLS0004 parsing accepts supported skeleton layouts and valid helper bones,
  associates animation chunks with the parsed rig, and preserves bind-pose
  rendering when optional rig data is absent or invalid.
- SceneScript treats modules without `update()` as init-only and preserves a
  property when `update()` performs side effects but returns `undefined`.
  Explicit `null` remains a distinct value.
- Puppet animation-layer lookup is a shared script API for image and model
  layers. The earlier 0.0.15 AppImage predates this interface. The 0.0.16
  release included the updated frontend and engine library, but its build did
  not collect the engine's external shared libraries, so some hosts stopped at
  loader error 127 before the renderer started. The release workflow now
  gathers that dependency closure and checks the engine against the staged
  libraries before creating the next AppImage. Checksums use a portable
  filename rather than the CI runner's absolute path.

The development build compiled successfully after these changes, and the
desktop application regression suite passed. A complete visual pass across
Workshop scenes is not claimed: in this environment, direct screenshot runs of
the extracted AppImage did not get a usable GLX display. Packaging checks
confirm that both the frontend and current engine are bundled, but do not
replace visual validation on a working graphics session.

## Complete net file inventory

The following list is the complete 95-path inventory from the current net diff
against upstream `main`, including the desktop application, integrated
packaging, renderer changes, this document, and the SceneScript API header.

### Build and automation

- `.github/workflows/appimage.yml` — AppImage build and tag-release workflow.
- `.github/workflows/cmake.yml` — desktop app checks, C++ build, formatting and CI.
- `.github/workflows/tests.yml` — engine unit-test workflow.
- `.gitignore` — ignores for the fork's app and packaging outputs.
- `CMakeLists.txt` — frontend/build integration and related target changes.

### Desktop application and user installation

- `README.md` — fork overview, dependencies, Steam discovery, desktop app and packaging instructions.
- `app/install.sh` — per-user install/removal helper.
- `app/linux-wallpaperengine-app.desktop` — desktop launcher metadata.
- `app/linux-wallpaperengine-app.service` — user systemd service.
- `app/linux-wallpaperengine-app.svg` — app icon.
- `app/tests/test_regressions.py` — desktop app regression coverage.
- `app/wallpaper_engine_app/__init__.py` — package initializer.
- `app/wallpaper_engine_app/__main__.py` — application entry point.
- `app/wallpaper_engine_app/backdrop.py` — requests KWin backdrop blur on KDE X11/XWayland after the GTK window is mapped. The dark glass uses stronger dark plum/charcoal tint and higher surface opacity to retain contrast while keeping blurred/color-shifted backdrop visible through the outer surface. Other compositors, native Wayland sessions, and desktops that do not honor the KWin X11 property show the tinted translucent surfaces without guaranteed blur; the GTK UI remains usable.
- `app/wallpaper_engine_app/daemon.py` — engine process and playlist control.
- `app/wallpaper_engine_app/gui.py` — GTK 4 interface.
- `app/wallpaper_engine_app/i18n.py` — language selection and translations.
- `app/wallpaper_engine_app/ipc.py` — local GUI/daemon messages.
- `app/wallpaper_engine_app/model.py` — Steam catalog and user configuration.
- `app/wallpaper_engine_app/previews.py` — preview loading, caching and GIF hover playback.
- `app/wallpaper_engine_app/theme.py` — GTK theme and accent preferences.

### Optional playlist rotation and AppImage

- `contrib/rotation/install.sh` — optional rotation service installer.
- `contrib/rotation/linux-wallpaperengine-rotation.service` — systemd unit.
- `contrib/rotation/wallpaper-engine-rotate.py` — standalone rotation helper.
- `packaging/appimage/AppRun` — AppImage runtime launcher.
- `packaging/appimage/README.md` — AppImage build/runtime documentation.
- `packaging/appimage/build-appimage.sh` — AppDir staging and packaging.

### Fork documentation

- `docs/FORK_CHANGES.md` — fork architecture, renderer changes, runtime behavior, packaging and upstream review context.

### Engine source: application, data and filesystem

- `src/main.cpp` — process startup changes.
- `src/Steam/FileSystem/FileSystem.cpp` — Steam library and asset path discovery.
- `src/Steam/FileSystem/FileSystem.h` — filesystem API updates.
- `src/WallpaperEngine/Application/ApplicationContext.cpp` — CLI and runtime configuration.
- `src/WallpaperEngine/Application/ApplicationContext.h` — application settings.
- `src/WallpaperEngine/Application/WallpaperApplication.cpp` — wallpaper launch and property setup.
- `src/WallpaperEngine/Application/WallpaperApplication.h` — application interface.
- `src/WallpaperEngine/Data/JSON.h` — JSON compatibility helpers.
- `src/WallpaperEngine/Data/Model/Material.h` — material fields.
- `src/WallpaperEngine/Data/Model/Object.h` — scene object variants and properties.
- `src/WallpaperEngine/Data/Model/Types.h` — dynamic model types.
- `src/WallpaperEngine/Data/Model/Wallpaper.h` — wallpaper/project settings.
- `src/WallpaperEngine/Data/Parsers/MaterialParser.cpp` — material parsing.
- `src/WallpaperEngine/Data/Parsers/ObjectParser.cpp` — object parsing and hierarchy.
- `src/WallpaperEngine/Data/Parsers/ObjectParser.h` — parser declarations.
- `src/WallpaperEngine/Data/Parsers/TextureParser.cpp` — texture parsing.
- `src/WallpaperEngine/Data/Parsers/WallpaperParser.cpp` — wallpaper/camera settings.

### Engine source: display, renderer and scripting

- `src/WallpaperEngine/Input/Drivers/GLFWMouseInput.cpp` — GLFW pointer state.
- `src/WallpaperEngine/Input/Drivers/WaylandMouseInput.cpp` — Wayland pointer/output state.
- `src/WallpaperEngine/Render/CTexture.cpp` — texture upload and resource handling.
- `src/WallpaperEngine/Render/CWallpaper.cpp` — wallpaper output/lifecycle.
- `src/WallpaperEngine/Render/CWallpaper.h` — wallpaper renderer interface.
- `src/WallpaperEngine/Render/Camera.cpp` — scene projection and scripted camera.
- `src/WallpaperEngine/Render/Camera.h` — camera state API.
- `src/WallpaperEngine/Render/FBOProvider.cpp` — framebuffer creation and sizing.
- `src/WallpaperEngine/Render/FBOProvider.h` — framebuffer declarations.
- `src/WallpaperEngine/Render/Objects/CCameraObject.h` — scene camera object.
- `src/WallpaperEngine/Render/Objects/CImage.cpp` — image transforms, effects and composition.
- `src/WallpaperEngine/Render/Objects/CImage.h` — image-layer API.
- `src/WallpaperEngine/Render/Objects/CModel.cpp` — model loading, matrices, skinning and drawing.
- `src/WallpaperEngine/Render/Objects/CModel.h` — model renderer declaration.
- `src/WallpaperEngine/Render/Objects/CParticle.cpp` — particle render state.
- `src/WallpaperEngine/Render/Objects/CRenderable.cpp` — shared renderable setup.
- `src/WallpaperEngine/Render/Objects/CText.cpp` — text rendering integration.
- `src/WallpaperEngine/Render/Objects/Effects/CPass.cpp` — shader pass setup and compatibility.
- `src/WallpaperEngine/Render/Objects/Effects/CPass.h` — shader pass interface.
- `src/WallpaperEngine/Render/Objects/PuppetModel.cpp` — skinned MDLV parsing and animation evaluation.
- `src/WallpaperEngine/Render/Objects/PuppetModel.h` — puppet model data structures.
- `src/WallpaperEngine/Render/PostProcessSettings.h` — post-process values.
- `src/WallpaperEngine/Render/Shaders/GLSLContext.cpp` — Workshop GLSL conversion.
- `src/WallpaperEngine/Render/Shaders/ShaderUnit.cpp` — shader compile/link handling.
- `src/WallpaperEngine/Render/Shaders/ShaderUnit.h` — shader unit declarations.
- `src/WallpaperEngine/Render/WallpaperState.cpp` — per-wallpaper render state.
- `src/WallpaperEngine/Render/WallpaperState.h` — render state API.
- `src/WallpaperEngine/Render/Wallpapers/CScene.cpp` — scene composition, camera and mouse mapping.
- `src/WallpaperEngine/Render/Wallpapers/CScene.h` — scene renderer interface.
- `src/WallpaperEngine/Render/Wallpapers/CVideo.cpp` — video wallpaper output.
- `src/WallpaperEngine/Render/Wallpapers/CVideo.h` — video renderer interface.
- `src/WallpaperEngine/Render/Wallpapers/CWeb.cpp` — web wallpaper output.
- `src/WallpaperEngine/Render/Wallpapers/CWeb.h` — web renderer interface.
- `src/WallpaperEngine/VideoPlayback/MPV/GLPlayer.cpp` — MPV OpenGL integration.
- `src/WallpaperEngine/VideoPlayback/MPV/GLPlayer.h` — MPV player state.
- `src/WallpaperEngine/Scripting/Adapters/ScriptableObjectAdapter.cpp` — SceneScript layer properties and animation-layer API.
- `src/WallpaperEngine/Scripting/Adapters/ScriptableObjectAdapter.h` — animation-layer interface and state.
- `src/WallpaperEngine/Scripting/Adapters/VectorAdapter.cpp` — vectors, angle units and vector methods.
- `src/WallpaperEngine/Scripting/Adapters/VectorAdapter.h` — vector adapter declaration.
- `src/WallpaperEngine/Scripting/EngineObject.cpp` — engine object bindings.
- `src/WallpaperEngine/Scripting/InputObject.cpp` — input bindings.
- `src/WallpaperEngine/Scripting/Modules/ColorModule.cpp` — color API.
- `src/WallpaperEngine/Scripting/Modules/MathModule.cpp` — math API.
- `src/WallpaperEngine/Scripting/Modules/MathModule.h` — math module declarations.
- `src/WallpaperEngine/Scripting/SceneObject.cpp` — SceneScript scene/camera functions.
- `src/WallpaperEngine/Scripting/ScriptEngine.cpp` — script loading and update behavior.
- `src/WallpaperEngine/Scripting/ScriptEngine.h` — script engine interface.
- `src/WallpaperEngine/Scripting/ScriptPropertiesObject.cpp` — script property handling.
- `src/WallpaperEngine/Scripting/ScriptableObject.cpp` — layer property registration.
- `src/WallpaperEngine/Scripting/ScriptableObject.h` — scriptable layer interface.

## Upstream pull request

Pull request [#683](https://github.com/Almamu/linux-wallpaperengine/pull/683)
targets `Almamu/linux-wallpaperengine:main` from the fork's feature branch. It
contains the full desktop application and packaging fork together with engine
compatibility changes. Reviewers can inspect the work by file group, including
the frontend and release workflow, SceneScript semantics, parent transforms,
model parsing and shader compatibility. The visual-validation limits above
remain explicit; parser success does not prove that every scene renders
identically to its Workshop preview.
