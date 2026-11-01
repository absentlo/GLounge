#!/bin/sh
# Builds the two downloads into dist/. Run by the release workflow.
set -eu
cd "$(dirname "$0")"

rm -rf dist
mkdir -p dist/GLounge
cp -r ./*.jsfx* README.md LICENSE docs dist/GLounge/

cp install.sh dist/GLounge/
tar -czf dist/GLounge-Linux.tar.gz -C dist GLounge
rm dist/GLounge/install.sh

cp install.bat dist/GLounge/
(cd dist && zip -qr GLounge-Windows.zip GLounge)

rm -rf dist/GLounge
