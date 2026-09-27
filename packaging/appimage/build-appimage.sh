#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD_DIR="${ROOT}/build"
OUTPUT_DIR="${ROOT}/build/appimage-output"
VERSION=""
APPIMAGETOOL_BIN="${APPIMAGETOOL:-appimagetool}"

fail() {
    printf '::error title=AppImage packaging::%s\n' "$*"
    printf '%s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: build-appimage.sh [--build-dir DIR] [--output-dir DIR] [--version VERSION] [--appimagetool FILE]

Packages an existing linux-wallpaperengine CMake build and the GTK desktop frontend.
EOF
}

while (($#)); do
    case "$1" in
        --build-dir)
            BUILD_DIR="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --version)
            VERSION="$2"
            shift 2
            ;;
        --appimagetool)
            APPIMAGETOOL_BIN="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

[[ "$(uname -m)" == x86_64 ]] || {
    printf 'This AppImage build currently supports x86_64 only.\n' >&2
    exit 1
}

[[ -x "${BUILD_DIR}/output/linux-wallpaperengine" ]] || {
    printf 'Engine build not found at %s/output/linux-wallpaperengine. Build the CMake target first.\n' "${BUILD_DIR}" >&2
    exit 1
}
[[ -f "${ROOT}/app/wallpaper_engine_app/__main__.py" ]] || {
    printf 'Frontend package is missing from %s/app.\n' "${ROOT}" >&2
    exit 1
}

if ! command -v "${APPIMAGETOOL_BIN}" >/dev/null 2>&1 && [[ ! -x "${APPIMAGETOOL_BIN}" ]]; then
    printf 'appimagetool was not found. Install AppImage/appimagetool 1.9.1 or pass --appimagetool.\n' >&2
    exit 1
fi

if [[ -z "${VERSION}" ]]; then
    VERSION="$(git -C "${ROOT}" describe --tags --always --dirty 2>/dev/null || printf 'dev')"
fi
[[ "${VERSION}" =~ ^[A-Za-z0-9._+-]+$ ]] || {
    printf 'Invalid version: %s\n' "${VERSION}" >&2
    exit 2
}

BUILD_DIR="$(cd -- "${BUILD_DIR}" && pwd)"
mkdir -p -- "${OUTPUT_DIR}"
OUTPUT_DIR="$(cd -- "${OUTPUT_DIR}" && pwd)"
APPDIR="$(mktemp -d "${BUILD_DIR}/linux-wallpaperengine-appimage.XXXXXXXX")"
ENGINE_DIR="${APPDIR}/usr/lib/linux-wallpaperengine"
FRONTEND_DIR="${APPDIR}/usr/lib/wallpaper_engine_app"
DESKTOP_FILE="linux-wallpaperengine-app.desktop"
OUTPUT_FILE="${OUTPUT_DIR}/linux-wallpaperengine-desktop-${VERSION}-x86_64.AppImage"

mkdir -p -- "${ENGINE_DIR}" "${FRONTEND_DIR}" \
    "${APPDIR}/usr/share/applications" \
    "${APPDIR}/usr/share/icons/hicolor/scalable/apps"

if ! cmake --install "${BUILD_DIR}" --prefix "${ENGINE_DIR}"; then
    fail 'CMake install failed; build all CMake targets before packaging.'
fi
python3 - "${ROOT}/app/wallpaper_engine_app" "${FRONTEND_DIR}" <<'PY'
from pathlib import Path
import shutil
import sys

shutil.copytree(
    Path(sys.argv[1]),
    Path(sys.argv[2]),
    dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
)
PY

install -Dm755 "${ROOT}/packaging/appimage/AppRun" "${APPDIR}/AppRun"
sed 's/^Exec=.*/Exec=AppRun/' "${ROOT}/app/linux-wallpaperengine-app.desktop" \
    > "${APPDIR}/usr/share/applications/${DESKTOP_FILE}"
install -Dm644 "${APPDIR}/usr/share/applications/${DESKTOP_FILE}" "${APPDIR}/${DESKTOP_FILE}"
install -Dm644 "${ROOT}/app/linux-wallpaperengine-app.svg" \
    "${APPDIR}/usr/share/icons/hicolor/scalable/apps/linux-wallpaperengine-app.svg"
install -Dm644 "${ROOT}/app/linux-wallpaperengine-app.svg" "${APPDIR}/linux-wallpaperengine-app.svg"

# CEF Release builds carry hundreds of megabytes of DWARF data that the runtime does not use.
[[ -f "${ENGINE_DIR}/libcef.so" ]] || fail "CEF runtime is missing from the install tree: ${ENGINE_DIR}/libcef.so"
strip --strip-debug "${ENGINE_DIR}/libcef.so" || fail 'Could not strip CEF debug sections.'

ENGINE_LIBRARY_PATH="${ENGINE_DIR}:${ENGINE_DIR}/lib:${ENGINE_DIR}/lib64"
for candidate in "${ENGINE_DIR}"/lib/* "${ENGINE_DIR}"/lib64/*; do
    [[ -d "${candidate}" ]] && ENGINE_LIBRARY_PATH+=":${candidate}"
done
RUNTIME_LDD="$(LD_LIBRARY_PATH="${ENGINE_LIBRARY_PATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
    ldd "${ENGINE_DIR}/linux-wallpaperengine")"
MISSING_LIBRARIES="$(printf '%s\n' "${RUNTIME_LDD}" | grep 'not found' || true)"
if [[ -n "${MISSING_LIBRARIES}" ]]; then
    printf '%s\n' "${RUNTIME_LDD}" >&2
    fail "The packaged engine has unresolved shared libraries: ${MISSING_LIBRARIES}"
fi

APPIMAGETOOL_APP_NAME='Linux Wallpaper Engine' \
APPIMAGE_EXTRACT_AND_RUN=1 \
ARCH=x86_64 \
VERSION="${VERSION}" \
    "${APPIMAGETOOL_BIN}" --no-appstream "${APPDIR}" "${OUTPUT_FILE}"

chmod 755 "${OUTPUT_FILE}"
sha256sum "${OUTPUT_FILE}" > "${OUTPUT_FILE}.sha256"
printf 'AppImage: %s\nAppDir: %s\n' "${OUTPUT_FILE}" "${APPDIR}"
