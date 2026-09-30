#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "==> Building JarvisMacAgent release binary..."
swift build -c release

BIN_PATH="$(swift build -c release --show-bin-path)/JarvisMacAgent"
APP_DIR="${SCRIPT_DIR}/JarvisMacAgent.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"

echo "==> Packaging into ${APP_DIR}..."
rm -rf "${APP_DIR}"
mkdir -p "${MACOS_DIR}"

cp "${BIN_PATH}" "${MACOS_DIR}/JarvisMacAgent"
cp "${SCRIPT_DIR}/Info.plist" "${CONTENTS_DIR}/Info.plist"
echo "APPL????" > "${CONTENTS_DIR}/PkgInfo"

# Ad-hoc code sign with entitlements
if [ -f "${SCRIPT_DIR}/JarvisMacAgent.entitlements" ]; then
    echo "==> Signing with entitlements..."
    codesign --force --sign - --entitlements "${SCRIPT_DIR}/JarvisMacAgent.entitlements" "${APP_DIR}"
else
    codesign --force --sign - "${APP_DIR}"
fi

echo "==> Successfully built ${APP_DIR}"
