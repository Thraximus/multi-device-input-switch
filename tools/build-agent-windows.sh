#!/usr/bin/env bash
# Build the self-contained Windows agent: embeddable Python + dependencies + agent code.
# Output goes where the hub serves it from: $SWITCHER_CONFIG_DIR/dist/windows/ (updates) and
# dist/agent-windows.zip (first install). Bump agent/VERSION so running agents pick it up.
# Python version: SWITCHER_WINDOWS_PYTHON in .env (an embeddable release from python.org).
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
. "$repo/tools/env.sh"
py_version="${SWITCHER_WINDOWS_PYTHON:-3.13.16}"
py_tag=$(echo "$py_version" | awk -F. '{print $1 $2}')
cache="$SWITCHER_CACHE_DIR"
dist="$SWITCHER_CONFIG_DIR/dist"
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
mkdir -p "$cache/wheels"

embed="$cache/python-$py_version-embed-amd64.zip"
[ -f "$embed" ] || curl -fL -o "$embed" "https://www.python.org/ftp/python/$py_version/python-$py_version-embed-amd64.zip"
# A private venv only to run pip (the system Python may not ship it).
[ -x "$cache/venv/bin/pip" ] || python3 -m venv "$cache/venv"
"$cache/venv/bin/pip" download --quiet --only-binary=:all: --platform win_amd64 --python-version "$py_tag" \
    --implementation cp -d "$cache/wheels" websockets cryptography pillow \
    winrt-runtime winrt-Windows.UI.Notifications winrt-Windows.Data.Xml.Dom winrt-Windows.Foundation \
    winrt-Windows.Foundation.Collections

root="$build/app"
mkdir -p "$root/python/Lib/site-packages"
python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$embed" "$root/python"
for whl in "$cache"/wheels/*.whl; do
    python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$whl" "$root/python/Lib/site-packages"
done
# The embeddable distribution ignores PYTHONPATH; its ._pth file is the search path. ".." is the
# install root, where the agent package lives.
printf 'python%s.zip\n.\nLib\\site-packages\n..\n' "$py_tag" > "$root/python/python$py_tag._pth"

mkdir -p "$root/agent"
cp "$repo"/agent/*.py "$root/agent/"
cp "$repo/agent/VERSION" "$root/VERSION"

mkdir -p "$dist"
rm -rf "$dist/windows"
cp -r "$root" "$dist/windows"
(cd "$root" && python3 -c "
import os, zipfile
with zipfile.ZipFile('$dist/agent-windows.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for d, _, files in os.walk('.'):
        for f in files:
            z.write(os.path.join(d, f), os.path.relpath(os.path.join(d, f), '.'))
")
echo "agent $(cat "$repo/agent/VERSION") built: $dist/agent-windows.zip ($(du -h "$dist/agent-windows.zip" | cut -f1))"
