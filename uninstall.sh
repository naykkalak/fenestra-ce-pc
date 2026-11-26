#!/usr/bin/env bash
# Fenestra This PC: removal for the current user.
# Part of the Fenestra series by naykkalak - https://github.com/naykkalak/fenestra-ce-pc
# Copyright (C) naykkalak (2026) for Fenestra This PC
# SPDX-License-Identifier: GPL-3.0-or-later
#   ./uninstall.sh          remove the component, its menu entry and its desktop shortcut
#   ./uninstall.sh --purge  also remove its settings and caches (window size, sections, network list, printers, scanners, update check)
set -euo pipefail

# remove the KDE Store list entry first (the helper is part of the component)
python3 -I "$HOME/.local/share/plasma/plasmoids/org.fenestra.cepc/contents/code/store_registry.py" remove 2>/dev/null || true
kpackagetool6 -t Plasma/Applet -r org.fenestra.cepc >/dev/null 2>&1 || true
# version 1.x files, in case they are still there
rm -f "$HOME/.local/bin/ce_pc_plasma.py"
rm -rf "$HOME/.local/share/fenestra/ce-pc-icones" "$HOME/.local/share/fenestra/ce-pc-langues"
rm -f "$HOME/.local/share/applications/fenestra-ce-pc.desktop"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
[ -n "$DESK" ] && [ "$DESK" != "$HOME" ] || DESK="$HOME/Desktop"
rm -f "$DESK/fenestra-ce-pc.desktop"
rm -f "$HOME/.config/fenestra/ce-pc-shortcut-created"
rmdir "$HOME/.local/share/fenestra" 2>/dev/null || true   # only if no other Fenestra component uses it

if [ "${1:-}" = "--purge" ]; then
    rm -f "$HOME"/.config/fenestra/ce-pc-*.json "$HOME"/.cache/fenestra/ce-pc-*
    rm -rf "$HOME/.local/share/icons/ce_pc_cache"
    rmdir "$HOME/.config/fenestra" "$HOME/.cache/fenestra" 2>/dev/null || true
    echo "Settings and caches removed."
fi
echo "\"This PC\" has been removed. Bookmarks it added to Dolphin's \"Remote\" section are left untouched."
