#!/bin/sh
# Installs GLounge. Usage: ./install.sh [REAPER resource folder]
set -eu

here=$(dirname "$0")
reaper=${1:-${XDG_CONFIG_HOME:-$HOME/.config}/REAPER}

if [ ! -d "$reaper/Effects" ]; then
  echo "REAPER's Effects folder was not found at: $reaper/Effects" >&2
  echo "Open REAPER once, or pass its folder: ./install.sh /path/to/REAPER" >&2
  exit 1
fi

mkdir -p "$reaper/Effects/GLounge"
for f in "$here"/*.jsfx*; do
  install -m 644 "$f" "$reaper/Effects/GLounge/"
done
echo "Installed to $reaper/Effects/GLounge. Restart REAPER, or press F5 in the FX browser."
