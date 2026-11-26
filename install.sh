#!/usr/bin/env bash
# Fenestra This PC: installation for the current user (no root needed).
# Part of the Fenestra series by naykkalak - https://github.com/naykkalak/fenestra-ce-pc
# Copyright (C) naykkalak (2026) for Fenestra This PC
# SPDX-License-Identifier: GPL-3.0-or-later
# Meant to be run once the component has been downloaded from the KDE Store (it then sits in
# ~/.local/share/plasma/plasmoids/org.fenestra.cepc); it also works from a folder downloaded from GitHub.
#   ./install.sh                   finish the installation: previous version removed, requirements,
#                                  menu entry, desktop shortcut "This PC"
#   ./install.sh --no-desktop-icon same, without the desktop shortcut
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ID="org.fenestra.cepc"
DESKTOP_ICON=1
[ "${1:-}" = "--no-desktop-icon" ] && DESKTOP_ICON=0

for f in metadata.json contents/code/ce_pc_plasma.py contents/code/shortcut.sh contents/icons contents/locale; do
    [ -e "$SRC/$f" ] || { echo "Error: $f not found next to install.sh. Nothing was installed."; exit 1; }
done
# Installed by the KDE Store (or already in place): the component is there, only the rest is done.
IN_PLACE=0
[ "$(readlink -f "$SRC")" = "$(readlink -f "$HOME/.local/share/plasma/plasmoids/$ID" 2>/dev/null || true)" ] && IN_PLACE=1
if [ "$IN_PLACE" = 0 ]; then
    command -v kpackagetool6 >/dev/null || { echo "Error: kpackagetool6 not found (KDE Plasma 6 is required). Nothing was installed."; exit 1; }
fi

# A previous version (1.x or 2.0) kept the program in ~/.local/bin and its icons and languages in
# ~/.local/share/fenestra. Everything now lives inside the component: the old files are removed.
OLD_FILES=("$HOME/.local/bin/ce_pc_plasma.py" "$HOME/.local/share/fenestra/ce-pc-icones" "$HOME/.local/share/fenestra/ce-pc-langues")
OLD=0
for f in "${OLD_FILES[@]}"; do [ -e "$f" ] && OLD=1; done
if [ "$OLD" = 1 ]; then
    echo "A previous version of This PC (1.x or 2.0) was found."
    echo "It will be uninstalled before installing This PC 2.1 (your settings are kept)."
    if [ -t 0 ]; then
        printf "Continue? [Y/n] "
        read -r answer || answer="n"
        case "$answer" in
            ""|[Yy]*) ;;
            *) echo "Nothing was changed."; exit 0 ;;
        esac
    fi
fi

echo "Checking requirements…"

# The window is written in QML: test that Python can really load it (PyQt6 + Qt Quick,
# Controls, Layouts and Kirigami), exactly like "This PC" does, without showing anything.
qml_ok() {
    QT_QPA_PLATFORM=offscreen timeout 30 python3 - <<'PY' >/dev/null 2>&1
import sys
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine
app = QGuiApplication(sys.argv)
engine = QQmlApplicationEngine()
engine.loadData(b"""
import QtQuick
import QtQuick.Controls as QQC
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
Item { }
""", QUrl("file:///fenestra-test.qml"))
sys.exit(0 if engine.rootObjects() else 1)
PY
}

# Packages that provide PyQt6 and the QML modules, per distribution.
qml_packages() {
    local id="" like=""
    if [ -r /etc/os-release ]; then
        id="$(. /etc/os-release; echo "${ID:-}")"
        like="$(. /etc/os-release; echo "${ID_LIKE:-}")"
    fi
    case " $id $like " in
        *" solus "*)                 echo "sudo eopkg install python-qt6" ;;
        *" fedora "*)                echo "sudo dnf install python3-pyqt6 kf6-kirigami" ;;
        *" debian "*|*" ubuntu "*)   echo "sudo apt install python3-pyqt6 python3-pyqt6.qtqml python3-pyqt6.qtquick qml6-module-qtquick-controls qml6-module-qtquick-layouts qml6-module-org-kde-kirigami" ;;
        *" arch "*)                  echo "sudo pacman -S --needed python-pyqt6 kirigami" ;;
        *suse*)                      echo "sudo zypper install python3-PyQt6 kf6-kirigami" ;;
        *)                           echo "" ;;
    esac
}

missing=0
if ! qml_ok; then
    echo "  MISSING  PyQt6 with its QML modules (Qt Quick, Controls, Layouts, Kirigami)."
    pip_qt="$(python3 -c "import PyQt6, os; print(os.path.dirname(PyQt6.__file__))" 2>/dev/null || true)"
    case "$pip_qt" in
        "$HOME"/*) echo "           Note: PyQt6 was installed with pip ($pip_qt). It brings its own copy of Qt,"
                   echo "           which cannot use your desktop's Kirigami: install your distribution's packages instead." ;;
    esac
    cmd="$(qml_packages)"
    if [ -n "$cmd" ]; then
        echo "  Command for your distribution:"
        echo "    $cmd"
        if [ -t 0 ]; then
            printf "  Install them now? [Y/n] "
            read -r answer || answer="n"
            case "$answer" in
                ""|[Yy]*) if sh -c "$cmd" && qml_ok; then
                              echo "  OK       PyQt6 and its QML modules are now installed."
                          else
                              missing=1
                          fi ;;
                *) missing=1 ;;
            esac
        else
            missing=1
        fi
    else
        echo "  Install your distribution's PyQt6 packages (with QtQml and QtQuick) and the Kirigami QML module."
        missing=1
    fi
fi
for cmd in lsblk udevadm; do
    command -v "$cmd" >/dev/null || { echo "  MISSING  $cmd (required)"; missing=1; }
done
# outilsFacultatifs (2.0) : the system tools that enable each feature. With your agreement, the missing
# ones are installed in one go with your distribution's package manager (administrator password asked).
# Nothing is installed without asking. Applications (Skanpage, Filelight) stay your choice.
distro() {
    local id="" like=""
    if [ -r /etc/os-release ]; then
        id="$(. /etc/os-release; echo "${ID:-}")"
        like="$(. /etc/os-release; echo "${ID_LIKE:-}")"
    fi
    case " $id $like " in
        *" solus "*) echo solus ;;
        *" fedora "*) echo fedora ;;
        *" debian "*|*" ubuntu "*) echo debian ;;
        *" arch "*) echo arch ;;
        *suse*) echo suse ;;
        *) echo "" ;;
    esac
}
# package providing a tool, per distribution (solus fedora debian arch suse)
package_for() {
    case "$1" in
        udisksctl)    set -- udisks udisks2 udisks2 udisks2 udisks2 ;;   # Solus: "udisks" (checked)
        eject)        set -- util-linux util-linux eject util-linux util-linux ;;
        smartctl)     set -- smartmontools smartmontools smartmontools smartmontools smartmontools ;;
        smbclient)    set -- samba samba-client smbclient smbclient samba-client ;;
        lpstat)       set -- cups cups-client cups-client cups cups-client ;;
        ipptool)      set -- cups cups-ipptool cups-ipp-utils cups cups-client ;;
        avahi-browse) set -- avahi avahi-tools avahi-utils avahi avahi-utils ;;
        scanimage)    set -- sane-backends sane-backends sane-utils sane sane-backends ;;
        pkexec)       set -- polkit polkit pkexec polkit polkit ;;
        mkfs.exfat)   set -- exfatprogs exfatprogs exfatprogs exfatprogs exfatprogs ;;
        mkfs.vfat)    set -- dosfstools dosfstools dosfstools dosfstools dosfstools ;;
        mkntfs)       set -- ntfs-3g ntfsprogs ntfs-3g ntfs-3g ntfsprogs ;;
        *) return ;;
    esac
    case "$(distro)" in
        solus) echo "$1" ;; fedora) echo "$2" ;; debian) echo "$3" ;; arch) echo "$4" ;; suse) echo "$5" ;;
    esac
}
install_cmd() {
    case "$(distro)" in
        solus)  echo "eopkg install -y" ;;
        fedora) echo "dnf install -y" ;;
        debian) echo "apt-get install -y" ;;
        arch)   echo "pacman -S --needed --noconfirm" ;;
        suse)   echo "zypper --non-interactive install" ;;
    esac
}
has() { command -v "$1" >/dev/null 2>&1 || [ -x "/usr/sbin/$1" ] || [ -x "/sbin/$1" ]; }

absent_tools=(); absent_pkgs=()
for pair in "udisksctl:CD/DVD mounting" "eject:ejecting media" \
            "smartctl:full SMART report, health and tests of disks in a USB enclosure" \
            "smbclient:network shares" "lpstat:printers" "ipptool:ink levels" \
            "avahi-browse:real printer status" "scanimage:scanners" \
            "pkexec:surface scan and USB disk tests" "mkfs.exfat:formatting in exFAT" \
            "mkfs.vfat:formatting in FAT32" "mkntfs:formatting in NTFS"; do
    cmd="${pair%%:*}"; what="${pair#*:}"
    has "$cmd" && continue
    pkg="$(package_for "$cmd")"
    echo "  optional $cmd not found — $what${pkg:+ (package: $pkg)}"
    absent_tools+=("$cmd")
    if [ -n "$pkg" ]; then
        case " ${absent_pkgs[*]} " in *" $pkg "*) ;; *) absent_pkgs+=("$pkg") ;; esac
    fi
done
for pair in "kwallet-query:remembered network passwords" "dolphin:opening folders" "xdg-open:opening files" \
            "busctl:disk health and formatting" "mkfs.ext4:formatting in ext4"; do
    cmd="${pair%%:*}"; what="${pair#*:}"
    has "$cmd" || echo "  optional $cmd not found — $what"
done
if [ "$missing" = 0 ] && [ ${#absent_pkgs[@]} -gt 0 ] && [ -n "$(install_cmd)" ] && [ -t 0 ]; then
    echo "  To enable every feature, these packages can be installed now:"
    echo "    sudo $(install_cmd) ${absent_pkgs[*]}"
    printf "  Install them now? (administrator password asked) [Y/n] "
    read -r answer || answer="n"
    case "$answer" in
        ""|[Yy]*)
            if sudo -v; then
                for pkg in "${absent_pkgs[@]}"; do   # one by one: a package missing on this distribution does not stop the others
                    if sudo $(install_cmd) "$pkg" >/dev/null 2>&1; then
                        echo "  OK       $pkg"
                    else
                        echo "  FAILED   $pkg (you can install it later with your software centre)"
                    fi
                done
            fi ;;
        *) echo "  Skipped. You can run ./install.sh again later to install them." ;;
    esac
fi
has skanpage || echo "  tip      Skanpage (KDE scanning app) is not installed: the scanner card cannot open it."
has filelight || echo "  tip      Filelight (KDE disk usage app) is not installed: the \"Analyze space\" button is hidden."
[ "$missing" = 0 ] || { echo "Install the missing items above, then run ./install.sh again. Nothing was installed."; exit 1; }

echo "Installing…"
if [ "$OLD" = 1 ]; then
    for f in "${OLD_FILES[@]}"; do rm -rf "$f"; done
    rmdir "$HOME/.local/share/fenestra" 2>/dev/null || true
    echo "  previous version uninstalled"
fi

# The component: already in place when it comes from the KDE Store; otherwise upgrade or install it
if [ "$IN_PLACE" = 1 ]; then
    echo "  component: $ID (installed from the KDE Store)"
else
    if ! kpackagetool6 -t Plasma/Applet -u "$SRC" >/dev/null 2>&1; then
        kpackagetool6 -t Plasma/Applet -i "$SRC" >/dev/null || { echo "Error: the component could not be installed."; exit 1; }
    fi
    echo "  component installed: $ID"
fi

# Menu entry and desktop shortcut, both pointing to the program stored in the component
[ "$DESKTOP_ICON" = 1 ] || export FENESTRA_NO_DESKTOP=1
sh "$HOME/.local/share/plasma/plasmoids/$ID/contents/code/shortcut.sh"
[ "$DESKTOP_ICON" = 1 ] && echo "  desktop shortcut created"

# Record the component in the KDE Store list of installed add-ons, so that Discover can offer its updates
if python3 -I "$HOME/.local/share/plasma/plasmoids/$ID/contents/code/store_registry.py" add "$HOME/.local/share/plasma/plasmoids/$ID" 2>/dev/null; then
    echo "  registered in the KDE Store list (updates in Discover)"
fi

echo "Done. Open \"This PC\" from the application menu or the desktop shortcut."
