#!/bin/sh
# Fenestra This PC — part of the Fenestra series by naykkalak
# https://github.com/naykkalak/fenestra-ce-pc
# Copyright (C) naykkalak (2026) for Fenestra This PC
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Run each time the component loads: removes the files of previous versions, keeps the menu entry pointing
# to the program of this component, and puts the "This PC" shortcut on the desktop once (never again after the user removes it).

HERE="$(cd "$(dirname "$0")" && pwd)"
PROGRAM="$HERE/ce_pc_plasma.py"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
MARK="${XDG_CONFIG_HOME:-$HOME/.config}/fenestra/ce-pc-shortcut-created"
ENTRY="$APPS/fenestra-ce-pc.desktop"

# Versions 1.0 to 2.0 kept the program in ~/.local/bin and its icons and languages in ~/.local/share/fenestra.
# Everything now lives inside this component, so those old files are removed (nothing happens if they are absent).
rm -f "$HOME/.local/bin/ce_pc_plasma.py"
rm -rf "$HOME/.local/share/fenestra/ce-pc-icones" "$HOME/.local/share/fenestra/ce-pc-langues"
rmdir "$HOME/.local/share/fenestra" 2>/dev/null

mkdir -p "$APPS"
TMP="$(mktemp)"
cat > "$TMP" <<DESKTOP
[Desktop Entry]
Type=Application
Name=This PC
Name[de]=Dieser PC
Name[es]=Este equipo
Name[fr]=Ce PC
Name[it]=Questo PC
Name[ja]=PC
Name[nl]=Deze pc
Name[pl]=Ten komputer
Name[pt_BR]=Este Computador
Name[ru]=Этот компьютер
Name[tr]=Bu bilgisayar
Name[zh_CN]=此电脑
Name[zh_TW]=本機
Comment=Drives, network locations, printers and devices at a glance
Comment[fr]=Disques, emplacements réseau, imprimantes et périphériques en un coup d'œil
Exec=python3 "$PROGRAM"
Icon=computer
Terminal=false
Categories=System;FileTools;
DESKTOP

# menu entry: rewritten only when it differs (the component may have moved or been updated)
if ! cmp -s "$TMP" "$ENTRY" 2>/dev/null; then
    install -m 644 "$TMP" "$ENTRY"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null
fi

# desktop shortcut: created once; if it already exists (older installation), only its command is updated
DESK="$(xdg-user-dir DESKTOP 2>/dev/null)"
[ -n "$DESK" ] && [ "$DESK" != "$HOME" ] || DESK="$HOME/Desktop"
LINK="$DESK/fenestra-ce-pc.desktop"
if [ -f "$LINK" ]; then
    cmp -s "$TMP" "$LINK" 2>/dev/null || install -m 755 "$TMP" "$LINK"
elif [ ! -e "$MARK" ] && [ -d "$DESK" ] && [ -z "${FENESTRA_NO_DESKTOP:-}" ]; then
    install -m 755 "$TMP" "$LINK"
fi
mkdir -p "$(dirname "$MARK")" && : > "$MARK"
rm -f "$TMP"
exit 0
