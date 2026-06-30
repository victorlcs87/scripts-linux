#!/usr/bin/env bash
# Constroi o AppImage do reforja.
#
# Fluxo: PyInstaller (--onedir) -> AppDir -> appimagetool.
# Variaveis de ambiente:
#   VERSION       versao a embutir no nome (default: 0.0.0-dev)
#   UPDATE_INFO   update info do AppImage (zsync). Quando definido, gera .zsync
#                 e habilita auto-update. Ex.:
#                 gh-releases-zsync|victorlcs87|scripts-linux|latest|Reforja-*-x86_64.AppImage.zsync
#   OUTDIR        diretorio de saida (default: dist)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

VERSION="${VERSION:-0.0.0-dev}"
OUTDIR="${OUTDIR:-${ROOT}/dist}"
ARCH="${ARCH:-x86_64}"
APPDIR="${ROOT}/build/AppDir"
OUTPUT="${OUTDIR}/Reforja-${VERSION}-${ARCH}.AppImage"

echo ">> Limpando builds anteriores"
rm -rf "${ROOT}/build" "${ROOT}/dist/reforja"
mkdir -p "${OUTDIR}"

echo ">> Congelando com PyInstaller"
python -m PyInstaller --noconfirm --clean packaging/reforja.spec

echo ">> Montando AppDir"
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin" \
         "${APPDIR}/usr/share/applications" \
         "${APPDIR}/usr/share/icons/hicolor/512x512/apps"
cp -a "${ROOT}/dist/reforja/." "${APPDIR}/usr/bin/"

install -m 0755 "${ROOT}/packaging/AppRun" "${APPDIR}/AppRun"
install -m 0644 "${ROOT}/packaging/reforja.desktop" "${APPDIR}/reforja.desktop"
install -m 0644 "${ROOT}/packaging/reforja.desktop" "${APPDIR}/usr/share/applications/reforja.desktop"
install -m 0644 "${ROOT}/assets/reforja.png" "${APPDIR}/reforja.png"
install -m 0644 "${ROOT}/assets/reforja.png" "${APPDIR}/usr/share/icons/hicolor/512x512/apps/reforja.png"

# appimagetool: usa o do PATH ou baixa o AppImage oficial.
APPIMAGETOOL="$(command -v appimagetool || true)"
if [ -z "${APPIMAGETOOL}" ]; then
    echo ">> Baixando appimagetool"
    TOOL="${ROOT}/build/appimagetool-${ARCH}.AppImage"
    curl -fsSL -o "${TOOL}" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
    chmod +x "${TOOL}"
    APPIMAGETOOL="${TOOL}"
fi

echo ">> Gerando AppImage: ${OUTPUT}"
EXTRA_ARGS=()
if [ -n "${UPDATE_INFO:-}" ]; then
    EXTRA_ARGS+=("-u" "${UPDATE_INFO}")
fi

# Em runners sem FUSE, --appimage-extract-and-run evita a necessidade de FUSE.
ARCH="${ARCH}" "${APPIMAGETOOL}" --appimage-extract-and-run \
    "${EXTRA_ARGS[@]}" "${APPDIR}" "${OUTPUT}"

# Quando ha update info, o appimagetool gera o .zsync no diretorio atual (ROOT)
# com o nome-base do AppImage. Movemos para OUTDIR para o release anexa-lo.
if [ -n "${UPDATE_INFO:-}" ]; then
    ZSYNC_NAME="$(basename "${OUTPUT}").zsync"
    if [ -f "${ROOT}/${ZSYNC_NAME}" ] && [ "${ROOT}/${ZSYNC_NAME}" != "${OUTDIR}/${ZSYNC_NAME}" ]; then
        mv -f "${ROOT}/${ZSYNC_NAME}" "${OUTDIR}/${ZSYNC_NAME}"
    fi
fi

echo ">> Pronto:"
ls -lh "${OUTPUT}"*
