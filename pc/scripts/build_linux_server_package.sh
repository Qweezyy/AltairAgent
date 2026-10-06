#!/usr/bin/env bash
# Builds the Linux backend package (no desktop window) the way CI does — on Ubuntu 22.04, so the
# binary runs on glibc 2.35 and newer — for installing the current, not yet released code on a
# server body. Run inside an ubuntu:22.04 container with the repository mounted at /src:
#
#   docker run --rm -v <repo>:/src -v altair-build-cache:/cache ubuntu:22.04 \
#       bash /src/pc/scripts/build_linux_server_package.sh
#
# The package lands in <repo>/pc/dist/server/.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive PIP_CACHE_DIR=/cache/pip UV_CACHE_DIR=/cache/uv \
       UV_PYTHON_INSTALL_DIR=/cache/python PYTHONUTF8=1
apt-get -o DPkg::Lock::Timeout=600 update -qq
apt-get -o DPkg::Lock::Timeout=600 install -y -qq curl ca-certificates rsync binutils zip unzip >/dev/null
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh >/dev/null
uv python install 3.12 >/dev/null
uv venv -q --python 3.12 /work/venv
. /work/venv/bin/activate
rsync -a --delete --exclude .git --exclude android --exclude 'pc/dist*' --exclude 'pc/build' \
      --exclude node_modules --exclude 'pc/logs' --exclude 'pc/storage' --exclude '__pycache__' \
      --exclude '*.pyc' --exclude 'For Claude Test.txt' --exclude 'pc/.env' /src/ /work/repo/
cd /work/repo/pc
uv pip install -q -r requirements.txt pyinstaller
python build_app.py --zip --no-shell
mkdir -p /src/pc/dist/server
version=$(cat ../VERSION)
cp dist/LocalAIAgent-${version}.zip /src/pc/dist/server/Altair-${version}-linux-x64.zip
echo "PACKAGE=/src/pc/dist/server/Altair-${version}-linux-x64.zip"
