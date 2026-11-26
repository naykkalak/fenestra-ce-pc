#!/usr/bin/env python3
# Fenestra This PC — part of the Fenestra series by naykkalak
# https://github.com/naykkalak/fenestra-ce-pc
# Copyright (C) naykkalak (2026) for Fenestra This PC
# SPDX-License-Identifier: GPL-3.0-or-later
"""This PC (Plasma/QML version) - automatically follows the Plasma colours."""

import configparser
import hashlib
import json
import re
import shutil
import socket
import os
import subprocess
import sys
import threading
import time

os.environ["QT_QUICK_CONTROLS_STYLE"] = "org.kde.desktop"

# fenestraLangues: translations in gettext format (one folder per language), English is the fallback
import gettext

# 2.1: the program is stored in the Plasma component (contents/code); its icons (contents/icons) and
# languages (contents/locale) sit next to it. The old per-user folders are only a fallback.
_CONTENTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IN_COMPONENT = os.path.isfile(os.path.join(_CONTENTS, "icons", "fenestra-logo.svg"))
LANG_DIR = os.path.join(_CONTENTS, "locale") if _IN_COMPONENT \
    else os.path.expanduser("~/.local/share/fenestra/ce-pc-langues")
ICONS_DIR = os.path.join(_CONTENTS, "icons") if _IN_COMPONENT \
    else os.path.expanduser("~/.local/share/fenestra/ce-pc-icones")
LANG_ALIAS = {"zh": "zh_CN", "zh_SG": "zh_CN",
              "zh_HK": "zh_TW", "zh_MO": "zh_TW"}


def langues_systeme():
    """Langues du système, de la préférée à la suivante, arrêtées à la première variante d'anglais
    (l'anglais est la langue d'origine des textes : rien à traduire)."""
    brut = []
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        valeur = os.environ.get(var, "")
        if valeur:
            brut = [p.split(".")[0].split("@")[0] for p in valeur.split(":")]
            break
    if not brut:
        try:
            from PyQt6.QtCore import QLocale
            brut = [l.replace("-", "_") for l in QLocale().uiLanguages()]
        except ImportError:   # PyQt6 not installed yet: English
            brut = []
    res = []
    for l in brut:
        if not l or l in ("C", "POSIX"):
            continue
        l = LANG_ALIAS.get(l, l)
        if l.split("_")[0] == "en":
            break
        if l not in res:
            res.append(l)
    return res


LANGUES = langues_systeme()
try:
    _TRAD = gettext.translation("ce-pc", LANG_DIR, languages=LANGUES, fallback=True) if LANGUES \
        else gettext.NullTranslations()
except Exception:
    _TRAD = gettext.NullTranslations()


def tr(texte):
    """Texte traduit dans la langue du système ; le texte anglais si la langue n'est pas connue."""
    return _TRAD.gettext(texte)


# ---- PyQt6 missing: offer to install it (plain Python only, PyQt6 is not needed here) ----
APP_NAME = "Fenestra This PC"
APP_ICON = os.path.join(ICONS_DIR, "fenestra-logo.svg")
# PyQt6 package name per distribution (Fedora, Debian, Ubuntu / Arch, Manjaro / Solus; openSUSE: pythonXYZ-PyQt6)
PYQT6_PACKAGES = ["python3-pyqt6", "python-pyqt6", "python3-PyQt6", "python-qt6",
                  "python%d%d-PyQt6" % sys.version_info[:2]]
IMPORT_ERROR = ""


def _bubble_gi(title, text, button=None):
    """Without notify-send: bubble sent over a D-Bus connection kept open (Plasma only shows the button that way).
    Returns True if the button was clicked, False if not, None if PyGObject is missing (another way is tried)."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        state = {"id": None, "click": False}
        loop = GLib.MainLoop()

        def signal(conn, sender, path, interface, name, params, data=None):
            args = params.unpack()
            if state["id"] is None or args[0] != state["id"]:
                return
            if name == "ActionInvoked" and args[1] == "yes":
                state["click"] = True
            loop.quit()   # clicked or closed

        for name in ("ActionInvoked", "NotificationClosed"):
            bus.signal_subscribe("org.freedesktop.Notifications", "org.freedesktop.Notifications", name,
                                 "/org/freedesktop/Notifications", None, Gio.DBusSignalFlags.NONE, signal, None)
        actions = ["yes", button] if button else []
        r = bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                          "org.freedesktop.Notifications", "Notify",
                          GLib.Variant("(susssasa{sv}i)", (APP_NAME, 0, APP_ICON, title, text, actions,
                                                           {"urgency": GLib.Variant("y", 2)}, -1)),
                          None, Gio.DBusCallFlags.NONE, -1, None)
        state["id"] = r.unpack()[0]
        if not button:
            return False
        GLib.timeout_add_seconds(600, loop.quit)
        loop.run()
        return state["click"]
    except Exception:
        return None


def _bubble_gdbus(title, text, button=None):
    """Without notify-send (e.g. openSUSE): the same bubble sent to Plasma with gdbus; with a button, waits for the click."""
    import select

    def q(t):
        return "'" + t.replace("\\", "\\\\").replace("'", "\\'") + "'"
    monitor = None
    try:
        if button:   # listen before sending, so the click is not missed
            monitor = subprocess.Popen(["gdbus", "monitor", "--session", "--dest", "org.freedesktop.Notifications"],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        actions = "[%s, %s]" % (q("yes"), q(button)) if button else "[]"
        r = subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                            "--object-path", "/org/freedesktop/Notifications",
                            "--method", "org.freedesktop.Notifications.Notify",
                            APP_NAME, "0", APP_ICON, title, text, actions, "{'urgency': <byte 2>}", "0"],
                           capture_output=True, text=True, timeout=20)
        m = re.search(r"uint32 (\d+)", r.stdout)
        if not button or not m:
            return False
        ident = m.group(1)
        buf = ""
        end = time.time() + 600
        while time.time() < end:
            if not select.select([monitor.stdout], [], [], 1)[0]:
                continue
            chunk = os.read(monitor.stdout.fileno(), 4096)
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")
            if re.search(r"ActionInvoked \(uint32 %s, 'yes'\)" % ident, buf):
                return True
            if re.search(r"NotificationClosed \(uint32 %s," % ident, buf):
                return False
        return False
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        if monitor is not None:
            monitor.kill()
            monitor.wait()


def _bubble(title, text, button=None):
    """Plasma bubble that stays on screen; with a button, returns True if it was clicked."""
    if not shutil.which("notify-send"):   # e.g. openSUSE: no notify-send
        result = _bubble_gi(title, text, button)
        if result is not None:
            return result
        if shutil.which("gdbus"):
            return _bubble_gdbus(title, text, button)
    cmd = ["notify-send", "-a", APP_NAME, "-i", APP_ICON, "-u", "critical"]
    if button:
        cmd += ["-A", "yes=" + button, "--wait"]
    try:
        r = subprocess.run(cmd + [title, text], capture_output=True, text=True)
        return bool(button) and r.stdout.strip() == "yes"
    except OSError:
        return False


def _pyqt6_package():
    """First PyQt6 package name that PackageKit knows on this distribution (or None)."""
    env = dict(os.environ, LANG="C", LC_ALL="C")
    for name in PYQT6_PACKAGES:
        try:
            r = subprocess.run(["pkcon", "resolve", name], capture_output=True, text=True, env=env, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if any(("\t" + name + "-") in l or (" " + name + "-") in l for l in r.stdout.splitlines()):
            return name
    return None


def _last_line(text):
    """Last useful line of an error message, short, for the bubble."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return lines[-1][:160] if lines else ""


def offer_pyqt6():
    title = tr("This PC")
    if os.environ.get("FENESTRA_PYQT6_INSTALLED"):   # just installed but still missing: stop here
        _bubble(title, tr("PyQt6 could not be installed.") + ("\n" + _last_line(IMPORT_ERROR) if IMPORT_ERROR else ""))
        return 1
    # Arch, Manjaro: no PackageKit but pacman (password asked by Plasma, through pkexec)
    arch = not shutil.which("pkcon") and bool(shutil.which("pacman")) and bool(shutil.which("pkexec"))
    # openSUSE: PackageKit refuses to install; zypper through pkexec (password asked by Plasma)
    suse = bool(shutil.which("zypper")) and bool(shutil.which("pkexec"))
    if not shutil.which("pkcon") and not arch and not suse:   # neither PackageKit nor pacman (e.g. Solus): say what to install
        _bubble(title, tr("No PyQt6 package was found for this system. Install the PyQt6 package for Python 3 with your package manager."))
        return 1
    if not _bubble(title, tr("PyQt6 is missing"), tr("Install PyQt6")):
        return 1
    _bubble(tr("Installing PyQt6…"), tr("Please wait…"))
    if arch:
        command = ["pkexec", "pacman", "-S", "--noconfirm", "--needed", "python-pyqt6"]
    elif suse:
        command = ["pkexec", "zypper", "--non-interactive", "install", "python%d%d-PyQt6" % sys.version_info[:2]]
    else:
        name = _pyqt6_package()
        if not name:
            _bubble(title, tr("No PyQt6 package was found for this system. Install the PyQt6 package for Python 3 with your package manager."))
            return 1
        command = ["pkcon", "install", "-y", name]
    code, detail = -1, ""
    for attempt in (1, 2):   # a 2nd attempt after 5 s if the first fails
        try:
            r = subprocess.run(command, capture_output=True, text=True, timeout=900)
            code = r.returncode
            detail = _last_line(r.stderr) or _last_line(r.stdout)
        except (OSError, subprocess.TimeoutExpired) as e:
            code, detail = -1, str(e)[:160]
        if code in (0, 5, 102, 103):
            break
        if code in (126, 127) and (arch or suse):   # password refused or window closed: not asked again
            break
        time.sleep(5)
    if code not in (0, 5, 102, 103):
        _bubble(title, tr("PyQt6 could not be installed.") + ("\n" + detail if detail else ""))
        return 1
    _bubble(tr("PyQt6 is installed"), title)
    os.environ["FENESTRA_PYQT6_INSTALLED"] = "1"   # safeguard: only one restart
    os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])   # This PC opens


try:
    from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot
    from PyQt6.QtGui import QIcon
    from PyQt6.QtQml import QQmlApplicationEngine
    from PyQt6.QtWidgets import QApplication
except ImportError as import_error:
    IMPORT_ERROR = str(import_error)
    # PyQt6 missing: a bubble with an "Install PyQt6" button (PackageKit, password asked by Plasma);
    # nothing is installed without that click and that password
    sys.exit(offer_pyqt6())


def point_decimal():
    from PyQt6.QtCore import QLocale
    try:
        return QLocale(LANGUES[0]).decimalPoint() if LANGUES else "."
    except Exception:
        return "."


DECIMAL = point_decimal()


def table_traductions(qml):
    """Traductions de tous les textes passés à trad() dans le QML : {texte anglais: texte traduit}."""
    res = {}
    for m in re.finditer(r'trad\("((?:[^"\\]|\\.)*)"', qml):
        res[m.group(1)] = tr(m.group(1))
    return res


# (label, xdg-user-dir type, fallback name, icon names to try)
USER_FOLDERS_SPEC = [
    ("Desktop", "DESKTOP", "Bureau",
     ["user-desktop", "folder-desktop", "folder"]),
    ("Documents", "DOCUMENTS", "Documents",
     ["folder-documents", "folder-text", "folder"]),
    ("Pictures", "PICTURES", "Images",
     ["folder-pictures", "folder-images", "folder"]),
    ("Music", "MUSIC", "Musique",
     ["folder-music", "folder-sound", "folder"]),
    ("Downloads", "DOWNLOAD", "Téléchargements",
     ["folder-downloads", "folder-download", "folder"]),
    ("Videos", "VIDEOS", "Vidéos",
     ["folder-videos", "folder-video", "folder"]),
]

ICON_CACHE_DIR = os.path.expanduser("~/.local/share/icons/ce_pc_cache")


def get_icon_theme_name():
    try:
        config = configparser.ConfigParser(strict=False)
        config.read(os.path.expanduser("~/.config/kdeglobals"))
        if "Icons" in config and "Theme" in config["Icons"]:
            return config["Icons"]["Theme"]
    except Exception:
        pass
    return "breeze"


def get_xdg_user_dir(xdg_type, fallback_name):
    try:
        out = subprocess.check_output(
            ["xdg-user-dir", xdg_type], stderr=subprocess.DEVNULL,
            text=True, timeout=3,
        ).strip()
        if out and os.path.isdir(out):
            return out
    except Exception:
        pass
    candidate = os.path.join(os.path.expanduser("~"), fallback_name)
    return candidate if os.path.isdir(candidate) else None


def list_user_folders():
    folders = []
    for label, xdg_type, fallback_name, icon_names in USER_FOLDERS_SPEC:
        path = get_xdg_user_dir(xdg_type, fallback_name)
        if not path:
            continue
        icon = next((n for n in icon_names if QIcon.hasThemeIcon(n)), "folder")
        folders.append({"label": tr(label), "path": path, "icon": icon})
    return folders


IGNORED_FSTYPES = {
    "tmpfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2",
    "efivarfs", "pstore", "securityfs", "debugfs", "tracefs",
    "configfs", "fusectl", "squashfs", "overlay", "autofs",
    "mqueue", "devpts", "binfmt_misc", "hugetlbfs", "bpf",
}
IGNORED_MOUNT_PREFIXES = (
    "/run/user", "/run/lock", "/run/credentials", "/run/systemd",
    "/sys", "/proc", "/dev", "/snap", "/tmp",
    # system partitions (boot, EFI): hidden, as on Windows
    "/boot", "/efi",
)


def go(n_bytes):
    return f"{n_bytes / (1024 ** 3):.1f}".replace(".", DECIMAL)


def taille(n_bytes):
    """Capacité lisible : Go avec une décimale sous 1 To, puis To avec trois chiffres significatifs."""
    g = n_bytes / (1024 ** 3)
    if round(g, 1) < 1024:
        return f"{g:.1f} {tr('GB')}".replace(".", DECIMAL)
    t = g / 1024
    dec = 2 if t < 10 else (1 if t < 100 else 0)
    return f"{t:.{dec}f} {tr('TB')}".replace(".", DECIMAL)


def nom_emplacement(loc):
    """Nom affiché d'un emplacement réseau : celui choisi par l'utilisateur, sinon « partage sur serveur »
    dans la langue du système (langueReseau)."""
    nom = (loc.get("name") or "").strip()
    if not nom or nom == "%s sur %s" % (loc["share"], loc["host"]):
        return tr("{share} on {host}").format(share=loc["share"], host=loc["host"])
    return nom


def get_mount_label(mountpoint, device):
    try:
        out = subprocess.check_output(
            ["lsblk", "-no", "LABEL", device],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        if out:
            return out
    except Exception:
        pass
    return tr("Local Disk")


def _sous_jacent(device):
    """Volume chiffré déverrouillé (dm-N) : la partition qui le porte (ex. /dev/sdb1) ; sinon le périphérique tel quel."""
    try:
        n = os.path.basename(os.path.realpath(device))
        if not n.startswith("dm-"):
            return device
        for _ in range(4):
            if not n.startswith("dm-"):
                break
            esclaves = os.listdir("/sys/class/block/%s/slaves" % n)
            if not esclaves:
                return device
            n = esclaves[0]
        return "/dev/" + n
    except OSError:
        return device


def get_base_disk_device(device):
    base = os.path.basename(_sous_jacent(device))
    m = re.match(r"^(sd[a-z]+|nvme\d+n\d+|mmcblk\d+)", base)
    return "/dev/" + m.group(1) if m else device


def is_removable(device):
    try:
        base = os.path.basename(_sous_jacent(device))
        m = re.match(r"^(sd[a-z]+|nvme\d+n\d+|mmcblk\d+)", base)
        if not m:
            return False
        with open(f"/sys/block/{m.group(1)}/removable") as f:
            return f.read().strip() == "1"
    except Exception:
        return False


_USB_CACHE = {}


def is_usb_connected(device):
    if device in _USB_CACHE:
        return _USB_CACHE[device]
    try:
        out = subprocess.check_output(
            ["lsblk", "-no", "TRAN", get_base_disk_device(device)],
            stderr=subprocess.DEVNULL, text=True, timeout=5,
        )
        result = any(t.strip().lower() == "usb" for t in out.splitlines())
    except Exception:
        result = False
    _USB_CACHE[device] = result
    return result


def list_real_disks_lsblk():
    try:
        out = subprocess.check_output(
            ["lsblk", "-J", "-b",
             "-o", "NAME,PATH,MOUNTPOINT,FSTYPE,SIZE,TYPE,LABEL"],
            stderr=subprocess.DEVNULL, text=True, timeout=5,
        )
        data = json.loads(out)
    except Exception:
        return []

    disks = []
    seen = set()

    def walk(devices):
        for dev in devices:
            children = dev.get("children") or []
            if children:
                walk(children)
            mountpoint = dev.get("mountpoint")
            if not mountpoint or mountpoint == "[SWAP]" or mountpoint in seen:
                continue
            if any(mountpoint.startswith(p) for p in IGNORED_MOUNT_PREFIXES):
                continue
            fstype = dev.get("fstype") or ""
            if fstype in IGNORED_FSTYPES:
                continue
            path = dev.get("path") or (
                f"/dev/{dev['name']}" if dev.get("name") else None)
            if not path or not path.startswith("/dev/"):
                continue
            try:
                usage = shutil.disk_usage(mountpoint)
            except (PermissionError, FileNotFoundError, OSError):
                continue
            if usage.total == 0:
                continue
            seen.add(mountpoint)
            label = dev.get("label") or get_mount_label(mountpoint, path)
            disks.append((label, mountpoint, path, usage, fstype))

    walk(data.get("blockdevices", []))
    return disks


# Icon lists used by the drive and network cards. They are looked up once, in the main
# thread, at start-up: Qt's icon theme must not be queried from the background threads
# (crash when closing the window while a background refresh was running).
DISK_ICON_LISTS = (
    ["drive-optical", "media-optical-dvd", "media-optical"],
    ["drive-removable-media"],
    ["drive-harddisk-root", "drive-harddisk"],
    ["drive-harddisk"],
    ["folder-network", "network-server", "network-workgroup", "folder-remote"],
)
_disk_icon_cache = {}


def _svg_du_theme(nom):
    """SVG du thème windows-modern pour ce nom d'icône ('' s'il n'y est pas) : dessiné à la taille exacte, net."""
    for base in (os.path.expanduser("~/.local/share/icons/windows-modern"), "/usr/share/icons/windows-modern"):
        try:
            for contexte in os.listdir(base):
                p = os.path.join(base, contexte, nom + ".svg")
                if os.path.isfile(p):
                    return p
        except OSError:
            pass
    return ""


def disk_icon(names):
    key = tuple(names)
    if key in _disk_icon_cache:
        return _disk_icon_cache[key]
    found = names[-1]
    # 2.0: the old PNG images in the cache folder are no longer used (stretched, they made the icons blurry);
    # the theme's vector icon is used instead, drawn at the exact size
    if threading.current_thread() is threading.main_thread():
        found = next((n for n in names if QIcon.hasThemeIcon(n)), names[-1])
    _disk_icon_cache[key] = found
    return found


def disque_present(path):
    """True si un disque est dans le lecteur optique (propriété udev ID_CDROM_MEDIA)."""
    try:
        out = subprocess.check_output(
            ["udevadm", "info", "--query=property", "--name=" + path],
            stderr=subprocess.DEVNULL, text=True, timeout=5)
        return any(l.strip() == "ID_CDROM_MEDIA=1" for l in out.splitlines())
    except Exception:
        return False


def lecteurs_optiques_sans_montage():
    """Lecteurs CD/DVD non montés (vides, ou avec un disque pas encore monté), pour les afficher quand même.
    Les lecteurs montés sont déjà dans la liste des disques."""   # lecteurOptique
    try:
        out = subprocess.check_output(
            ["lsblk", "-J", "-o", "NAME,PATH,TYPE,MOUNTPOINT,FSTYPE,LABEL"],
            stderr=subprocess.DEVNULL, text=True, timeout=5)
        data = json.loads(out)
    except Exception:
        return []
    res = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "rom" or dev.get("mountpoint"):
            continue
        path = dev.get("path") or ("/dev/" + (dev.get("name") or ""))
        if not path.startswith("/dev/"):
            continue
        present = disque_present(path) or bool(dev.get("fstype"))
        nom = dev.get("label") if (present and dev.get("label")) else tr("CD/DVD Drive")
        res.append({
            "rawLabel": nom, "label": nom, "mountpoint": "optical:" + path, "device": path,
            "fstype": dev.get("fstype") or "", "total": 0, "used": 0, "free": 0,
            "icon": disk_icon(["drive-optical", "media-optical-dvd", "media-optical"]),
            "isKey": False, "isUsb": False, "isOptical": True,
            "freeText": tr("Available") if present else tr("No disc"),
            "health": "", "healthText": "",
        })
    return res


# santeDisque (This PC 1.1): SMART state watched by udisks2, readable without administrator rights.
# Result: "" (good or unknown), "warn" (signs of wear), "fail" (failing disk).
# To try the display: CEPC_SANTE_TEST=warn or fail forces the state of the system disk.
_SANTE = {}   # device name (sda2, nvme0n1p2…) -> (time of the reading, state)
_UDISKS = "org.freedesktop.UDisks2"


def _udisks_prop(objet, interface, nom):
    try:
        r = subprocess.run(["busctl", "--system", "--json=short", "get-property", _UDISKS, objet, interface, nom],
                           capture_output=True, text=True, timeout=3)
        return json.loads(r.stdout).get("data") if r.returncode == 0 else None
    except Exception:
        return None


_CHIFFRE = {}   # padlock (2.0): device -> encrypted volume (unlocked LUKS) or not


def is_encrypted(device):
    """True si le volume est un disque chiffré (LUKS) déverrouillé : type « crypt » pour lsblk."""
    if device not in _CHIFFRE:
        try:
            r = subprocess.run(["lsblk", "-dno", "TYPE", device], capture_output=True, text=True, timeout=3)
            _CHIFFRE[device] = r.stdout.strip() == "crypt"
        except Exception:
            return False
    return _CHIFFRE[device]


def disk_health(device, mountpoint=""):
    test = os.environ.get("CEPC_SANTE_TEST", "")
    if mountpoint == "/" and test in ("warn", "fail"):
        return test
    nom = os.path.basename(os.path.realpath(device))
    if not re.match(r"^[A-Za-z0-9_]+$", nom):
        return ""
    lu = _SANTE.get(nom)
    if lu and time.time() - lu[0] < 300:   # read again every 5 minutes at most (udisks2 does not refresh it more often)
        return lu[1]
    _SANTE[nom] = (time.time(), "")
    drive = _udisks_prop("/org/freedesktop/UDisks2/block_devices/" + nom, _UDISKS + ".Block", "Drive")
    if not drive or drive == "/":
        return ""
    etat = ""
    ata = _UDISKS + ".Drive.Ata"
    if _udisks_prop(drive, ata, "SmartSupported") and _udisks_prop(drive, ata, "SmartEnabled"):
        if _udisks_prop(drive, ata, "SmartFailing"):
            etat = "fail"
        elif (_udisks_prop(drive, ata, "SmartNumBadSectors") or 0) > 0 \
                or (_udisks_prop(drive, ata, "SmartNumAttributesFailing") or 0) > 0:
            etat = "warn"
    else:
        alertes = _udisks_prop(drive, _UDISKS + ".NVMe.Controller", "SmartCriticalWarning")
        if isinstance(alertes, list) and alertes:
            etat = "warn" if set(alertes) <= {"temperature"} else "fail"
    _SANTE[nom] = (time.time(), etat)
    return etat


HEALTH_TEXT = {"warn": "Signs of wear – see Properties", "fail": "Disk failing – back up your files"}


# santeDetails (This PC 2.0): "Health" tab of the properties, read by udisks2 without administrator rights.
# NVMe: standardized attributes (all brands). SATA: only the attributes that are reliable from one maker
# to another are kept (the amount of data written is not: never shown). Missing value = -1.
_SELFTEST_OK = ("success", "aborted", "interrupted", "inprogress", "")


def annulation_polkit(e):
    """True si l'erreur vient de la fenêtre du mot de passe annulée ou fermée (pas une vraie panne).
    Selon les versions de polkit/udisks2 : « …NotAuthorizedDismissed » ou « Not authorized to perform operation »."""
    s = str(e)
    return "Dismissed" in s or "NotAuthorized" in s or "Not authorized" in s


def _udisks_call(objet, interface, methode, signature, *args, interactif=False, delai=10):
    cmd = ["busctl", "--system", "--json=short"]
    if interactif:   # lets polkit ask for the administrator password
        cmd += ["--allow-interactive-authorization=yes", "--timeout=%d" % max(120, delai)]
    cmd += ["call", _UDISKS, objet, interface, methode, signature] + [str(a) for a in args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=delai)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return json.loads(r.stdout).get("data") if r.stdout.strip() else None


def _udisks_tout(objet, interface):
    try:
        d = _udisks_call(objet, "org.freedesktop.DBus.Properties", "GetAll", "s", interface)
        return {k: v.get("data") for k, v in d[0].items()}
    except Exception:
        return {}


def _drive_of(device):
    nom = os.path.basename(os.path.realpath(device))
    if not re.match(r"^[A-Za-z0-9_]+$", nom):
        return ""
    drive = _udisks_prop("/org/freedesktop/UDisks2/block_devices/" + nom, _UDISKS + ".Block", "Drive")
    return drive if drive and drive != "/" else ""


def _kelvin(k):
    try:
        return int(round(float(k) - 273.15)) if float(k) > 0 else -999
    except (TypeError, ValueError):
        return -999


# identiteDisque (2.0): brand deduced from the start of the model name (udisks2 often leaves the brand empty)
_MARQUES = (("SAMSUNG", "Samsung"), ("WDC", "Western Digital"), ("WD", "Western Digital"),
            ("SEAGATE", "Seagate"), ("ST", "Seagate"), ("CRUCIAL", "Crucial"), ("CT", "Crucial"),
            ("MICRON", "Micron"), ("TS", "Transcend"), ("TRANSCEND", "Transcend"), ("KINGSTON", "Kingston"),
            ("SA400", "Kingston"), ("SKC", "Kingston"), ("INTEL", "Intel"), ("SSDPE", "Intel"),
            ("SSDSC", "Intel"), ("HGST", "HGST"), ("HITACHI", "Hitachi"), ("TOSHIBA", "Toshiba"),
            ("KIOXIA", "Kioxia"), ("SANDISK", "SanDisk"), ("SK HYNIX", "SK hynix"), ("HFS", "SK hynix"),
            ("MAXTOR", "Maxtor"), ("LITEON", "Lite-On"), ("LITE-ON", "Lite-On"), ("ADATA", "ADATA"),
            ("PNY", "PNY"), ("CORSAIR", "Corsair"), ("SABRENT", "Sabrent"), ("PATRIOT", "Patriot"),
            ("TEAM", "Teamgroup"), ("LEXAR", "Lexar"), ("APPLE", "Apple"), ("VBOX", "VirtualBox"),
            ("QEMU", "QEMU"), ("VMWARE", "VMware"))


def disk_identity(vendor, model):
    """(marque, modèle) : la marque est retirée du modèle quand elle y figure en premier mot."""
    vendor, model = (vendor or "").strip(), (model or "").strip()
    haut = model.upper()
    if vendor and vendor.upper() not in ("ATA", "USB", "GENERIC"):   # brand given by the disk
        if haut.startswith(vendor.upper() + " "):
            model = model[len(vendor):].strip()
        return vendor, model
    for prefixe, nom in _MARQUES:
        if haut.startswith(prefixe):
            if haut.startswith(nom.upper() + " ") or haut.startswith(prefixe + " "):
                model = model.split(" ", 1)[1].strip()
            return nom, model
    return "", model


def _selftest_job(drive):
    """True si udisks2 garde encore une tâche d'autotest pour ce disque. Il ne la libère qu'à sa propre
    relecture du disque, parfois une minute après la fin réelle du test : d'ici là, il refuse un nouveau test."""
    try:
        r = subprocess.run(["busctl", "--system", "tree", "--list", _UDISKS], capture_output=True, text=True, timeout=3)
        for objet in r.stdout.split():
            if "/jobs/" not in objet:
                continue
            j = _udisks_tout(objet, _UDISKS + ".Job")
            if "selftest" in (j.get("Operation") or "") and drive in (j.get("Objects") or []):
                return True
    except Exception:
        return None   # reading impossible: unknown state (the window keeps its current state)
    return False


def _attr_brut(table, num):
    for a in table:
        if a.get("id") == num:
            return a
    return None


def health_usb(device):
    """santeUsb (2.0) : disque dans un boîtier USB. udisks2 ne lit pas sa santé, smartctl si (mode « sat »).
    Lecture seule, avec le mot de passe administrateur (pkexec). Même format que health_details()."""
    s = health_details(device)
    base = get_base_disk_device(device)
    if not re.match(r"^/dev/[A-Za-z0-9]+$", base) or not shutil.which("smartctl"):
        return s, "absent"
    try:
        r = subprocess.run(["pkexec", shutil.which("smartctl"), "-j", "-i", "-H", "-A", "-d", "sat", base],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return s, "erreur"
    if r.returncode in (126, 127) and not r.stdout.strip():   # password cancelled or refused
        return s, "annule"
    try:
        j = json.loads(r.stdout)
    except ValueError:
        return s, "erreur"
    if "smart_status" not in j:   # the enclosure does not pass the information through
        return s, "bloque"
    table = (j.get("ata_smart_attributes") or {}).get("table") or []
    rot = j.get("rotation_rate", 0)
    s.update({"available": True, "viaUsb": True, "kind": "hdd" if isinstance(rot, int) and rot > 0 else "ssd",
              "hours": (j.get("power_on_time") or {}).get("hours", -1),
              "cycles": j.get("power_cycle_count", -1),
              "temp": (j.get("temperature") or {}).get("current", -999),
              "selftest": "", "selftestLeft": -1})
    if j.get("model_name"):
        s["brand"], s["model"] = disk_identity("", j["model_name"])
    if j.get("firmware_version"):
        s["firmware"] = j["firmware_version"]
    defauts = 0
    for num in (5, 197, 198):   # reallocated, pending and unreadable sectors
        a = _attr_brut(table, num)
        if a:
            defauts += (a.get("raw") or {}).get("value", 0)
    s["errors"] = defauts
    if s["kind"] == "ssd":
        for num in (231, 232, 233, 202):
            a = _attr_brut(table, num)
            if a and 0 <= a.get("value", -1) <= 100:
                s["lifeUsed"] = 100 - a["value"]
                break
    raisons = []
    if not (j.get("smart_status") or {}).get("passed", True):
        raisons.append("fail:critical")
    if defauts > 0:
        raisons.append("warn:sectors")
    if s["temp"] >= 60:
        raisons.append("warn:temp")
    if s["lifeUsed"] >= 90:
        raisons.append("warn:life")
    s["reasons"] = raisons
    s["state"] = "fail" if any(x.startswith("fail") for x in raisons) else ("warn" if raisons else "good")
    return s, ""


# testsUsb (2.0): self-test of a disk in a USB enclosure, through smartctl ("sat" mode). A single password:
# this small program (started with pkexec) starts the test, reads the progress every 3 s and writes it
# on its output ("prog <% left>", then "fin <code>"). "stop" on its input stops the test.
USB_TESTEUR = r'''
import json, subprocess, sys, threading, time
dev, genre, sm = sys.argv[1], sys.argv[2], sys.argv[3]
arret = threading.Event()
def ecoute():
    for ligne in sys.stdin:
        if ligne.strip() == "stop":
            subprocess.run([sm, "-d", "sat", "-X", dev], capture_output=True)
    arret.set()
threading.Thread(target=ecoute, daemon=True).start()
def etat():
    try:
        j = json.loads(subprocess.run([sm, "-j", "-d", "sat", "-c", dev], capture_output=True, text=True).stdout)
        return (j.get("ata_smart_data") or {}).get("self_test", {}).get("status", {})
    except Exception:
        return {}
r = subprocess.run([sm, "-d", "sat", "-t", genre, dev], capture_output=True, text=True)
if "has begun" not in r.stdout:
    print("refus", flush=True)
    sys.exit(0)
vu, debut = False, time.time()
while True:
    st = etat()
    v = st.get("value", 0)
    if v >> 4 == 15:   # 0xF_: test in progress
        vu = True
        print("prog", st.get("remaining_percent", 100), flush=True)
    elif vu or time.time() - debut > 30:
        print("fin", v >> 4, flush=True)
        break
    time.sleep(3)
'''


# formatage (2.0): volumes the system needs, never formattable from This PC
_POINTS_SYSTEME = ("/", "/boot", "/boot/efi", "/efi", "/home", "/usr", "/var")


def device_mountpoints(device):
    try:
        r = subprocess.run(["findmnt", "-rno", "TARGET", "-S", device], capture_output=True, text=True, timeout=5)
        return [l for l in r.stdout.splitlines() if l]
    except Exception:
        return []


def volume_systeme(device):
    """True si le volume porte une partie du système (ou de l'échange) : formatage refusé."""
    if any(m in _POINTS_SYSTEME for m in device_mountpoints(device)):
        return True
    try:
        with open("/proc/swaps") as f:
            if any(l.split()[0] == os.path.realpath(device) for l in f.readlines()[1:] if l.strip()):
                return True
    except OSError:
        pass
    return False


def health_details(device, rafraichir=False):
    s = {"available": False, "kind": "", "usb": False, "viaUsb": False, "state": "", "reasons": [], "jobBusy": False,
         "lifeUsed": -1, "spare": -1, "spareThresh": -1, "errors": -1, "hours": -1,
         "cycles": -1, "unsafe": -1, "written": -1.0, "read": -1.0,
         "temp": -999, "tempWarn": -999, "selftest": "", "selftestLeft": -1}
    drive = _drive_of(device)
    if not drive:
        return s
    s["jobBusy"] = _selftest_job(drive)
    if rafraichir:   # during a self-test: immediate reading by the disk (otherwise every ~10 min)
        for iface in (".NVMe.Controller", ".Drive.Ata"):
            try:
                _udisks_call(drive, _UDISKS + iface, "SmartUpdate", "a{sv}", 1, "auth.no_user_interaction", "b", "true")
                break
            except Exception:
                pass
    drv = _udisks_tout(drive, _UDISKS + ".Drive")
    s["usb"] = drv.get("ConnectionBus") == "usb"
    s["brand"], s["model"] = disk_identity(drv.get("Vendor"), drv.get("Model"))
    s["firmware"] = (drv.get("Revision") or "").strip()
    s["serial"] = (drv.get("Serial") or "").strip()
    rot = drv.get("RotationRate", 0)
    s["kind"] = "ssd" if rot == 0 else ("hdd" if rot > 0 else "")   # -1: unknown (often USB)
    raisons = []
    nv = _udisks_tout(drive, _UDISKS + ".NVMe.Controller")
    at = _udisks_tout(drive, _UDISKS + ".Drive.Ata")
    if nv:
        s["kind"] = "nvme"
        if not nv.get("SmartUpdated"):
            return s
        try:
            a = {k: v.get("data") for k, v in
                 _udisks_call(drive, _UDISKS + ".NVMe.Controller", "SmartGetAttributes", "a{sv}", 0)[0].items()}
        except Exception:
            a = {}
        s["available"] = True
        s["lifeUsed"] = a.get("percent_used", -1)
        s["spare"] = a.get("avail_spare", -1)
        s["spareThresh"] = a.get("spare_thresh", -1)
        s["errors"] = a.get("media_errors", -1)
        s["hours"] = nv.get("SmartPowerOnHours", -1)
        s["cycles"] = a.get("power_cycles", -1)
        s["unsafe"] = a.get("unsafe_shutdowns", -1)
        s["written"] = float(a.get("total_data_written", -1))
        s["read"] = float(a.get("total_data_read", -1))
        s["temp"] = _kelvin(nv.get("SmartTemperature"))
        s["tempWarn"] = _kelvin(a.get("wctemp"))
        s["selftest"] = nv.get("SmartSelftestStatus", "")
        s["selftestLeft"] = nv.get("SmartSelftestPercentRemaining", -1)
        alertes = nv.get("SmartCriticalWarning") or []
        if set(alertes) - {"temperature"}:
            raisons.append("fail:critical")
        if 0 <= s["spare"] < s["spareThresh"]:
            raisons.append("fail:spare")
        if "temperature" in alertes:
            raisons.append("warn:temp")
    elif at.get("SmartSupported") and at.get("SmartEnabled") and at.get("SmartUpdated"):
        try:
            liste = _udisks_call(drive, _UDISKS + ".Drive.Ata", "SmartGetAttributes", "a{sv}", 0)[0]
        except Exception:
            liste = []
        attr = {x[0]: x for x in liste if isinstance(x, list) and len(x) >= 8}
        s["kind"] = s["kind"] or "hdd"
        s["available"] = True
        if s["kind"] == "ssd":
            for num in (231, 232, 233, 202):   # remaining life: the first attribute present
                x = attr.get(num)
                if not x:
                    continue
                reste = x[6] if (num == 232 and x[7] == 6) else x[3]   # 6 = percentage
                if 0 <= reste <= 100:
                    s["lifeUsed"] = 100 - reste
                    break
        s["errors"] = max(-1, at.get("SmartNumBadSectors", -1))
        sec = at.get("SmartPowerOnSeconds", 0)
        s["hours"] = sec // 3600 if sec else -1
        if 12 in attr and attr[12][7] == 1:
            s["cycles"] = attr[12][6]
        s["temp"] = _kelvin(at.get("SmartTemperature"))
        s["selftest"] = at.get("SmartSelftestStatus", "")
        s["selftestLeft"] = at.get("SmartSelftestPercentRemaining", -1)
        if at.get("SmartFailing"):
            raisons.append("fail:critical")
        if s["errors"] > 0 or at.get("SmartNumAttributesFailing", 0) > 0:
            raisons.append("warn:sectors")
        if s["temp"] >= 60:
            raisons.append("warn:temp")
    else:
        return s
    if s["lifeUsed"] >= 90:
        raisons.append("warn:life")
    if s["kind"] == "nvme" and s["errors"] > 0:
        raisons.append("warn:sectors")
    if s["selftest"] not in _SELFTEST_OK:
        raisons.append("warn:test")
    s["reasons"] = raisons
    s["state"] = "fail" if any(r.startswith("fail") for r in raisons) else ("warn" if raisons else "good")
    return s



# analyseSurface (This PC 2.0): complete reading of the disk, zone by zone, WITHOUT ANY WRITING.
# This small program is started with pkexec (reading a whole disk requires administrator rights).
# It stops if "stop" is written to it or if This PC closes (standard input closed).
SURFACE_ZONES = 1000   # 40 x 25 squares grid
SURFACE_LECTEUR = r'''
import errno, fcntl, mmap, os, struct, sys, threading, time
dev, n = sys.argv[1], int(sys.argv[2])
arret = threading.Event()
def ecoute():
    for ligne in sys.stdin:
        if ligne.strip() == "stop":
            break
    arret.set()
threading.Thread(target=ecoute, daemon=True).start()
direct = True
try:
    fd = os.open(dev, os.O_RDONLY | os.O_DIRECT)
except OSError:
    fd, direct = os.open(dev, os.O_RDONLY), False
taille = os.lseek(fd, 0, os.SEEK_END)
try:   # size of a logical sector (512 or 4096 bytes)
    ss = struct.unpack("i", fcntl.ioctl(fd, 0x1268, b"\0" * 4))[0] or 512
except OSError:
    ss = 512
print("taille", taille, ss, flush=True)
tampon = mmap.mmap(-1, 4 << 20)
zone = max(1 << 20, (taille // n) >> 20 << 20)
for i in range(n):
    if arret.is_set():
        break
    debut = i * zone
    fin = taille if i == n - 1 else min(taille, (i + 1) * zone)
    if debut >= fin:
        print("zone", i, 0, "0", 0, flush=True)
        continue
    t0, err, pos = time.monotonic(), 0, debut
    while pos < fin and not arret.is_set():
        long = min(len(tampon), fin - pos)
        try:
            lu = os.preadv(fd, [memoryview(tampon)[:long]], pos)
            if lu <= 0:
                break
            pos += lu
        except OSError as e:
            if e.errno == errno.EINVAL and direct:   # direct reading refused: read again normally
                os.close(fd)
                fd, direct = os.open(dev, os.O_RDONLY), False
                continue
            # error in this block: read again sector by sector to count the unreadable sectors
            for p in range(pos, pos + long, ss):
                if arret.is_set():
                    break
                try:
                    os.preadv(fd, [memoryview(tampon)[:ss]], p)
                except OSError:
                    err += 1
            pos += long
    try:
        os.posix_fadvise(fd, debut, fin - debut, os.POSIX_FADV_DONTNEED)
    except OSError:
        pass
    if arret.is_set():   # interrupted zone: not counted
        break
    print("zone", i, fin - debut, "%.4f" % (time.monotonic() - t0), err, flush=True)
print("fin", flush=True)
'''


def surface_classer(zones):
    """États des zones : 0 non lue, 1 normale, 2 lente (moins de 40 % de la vitesse médiane), 3 erreur."""
    vitesses = sorted(z[0] / z[1] for z in zones if z and z[1] > 0 and not z[2])
    mediane = vitesses[len(vitesses) // 2] if len(vitesses) >= 10 else 0
    etats = []
    for z in zones:
        if not z:
            etats.append(0)
        elif z[2]:
            etats.append(3)
        elif mediane and z[1] > 0 and z[0] / z[1] < 0.4 * mediane:
            etats.append(2)
        else:
            etats.append(1)
    return etats


def list_real_disks():
    result = []
    for label, mountpoint, device, usage, fstype in list_real_disks_lsblk():
        raw_label = label
        optical = device.startswith("/dev/sr")
        removable = is_removable(device)
        usb = is_usb_connected(device)
        if removable and not optical and label == tr("Local Disk"):   # disqueAmovible
            label = tr("Removable Disk")
        if usb and not removable and not optical:
            label = "USB-" + label
        if optical:
            icon = disk_icon(["drive-optical", "media-optical-dvd", "media-optical"])
        elif removable:   # removable disk (2.0): disk icon with a USB mark, drawn for Fenestra
            usb_img = os.path.join(ICONS_DIR, "fenestra-disque-amovible.svg")   # vector: sharp at any size
            # only with the windows-modern theme: same style as the other disks; otherwise the theme's icon
            if os.path.isfile(usb_img) and _svg_du_theme("drive-harddisk"):
                icon = "file://" + usb_img
            else:
                icon = disk_icon(["drive-removable-media"])
        elif mountpoint == "/":
            icon = disk_icon(["drive-harddisk-root", "drive-harddisk"])
        else:
            icon = disk_icon(["drive-harddisk"])
        result.append({
            "rawLabel": raw_label,
            "label": label, "mountpoint": mountpoint, "device": device,
            "fstype": fstype, "total": usage.total, "used": usage.used,
            "free": usage.free, "icon": icon, "isKey": removable,
            "isUsb": (removable or usb) and not optical,
            "isOptical": optical,
            "freeText": tr("{free} free of {total}").format(free=taille(usage.free), total=taille(usage.total)),
            "health": "" if optical else disk_health(device, mountpoint),
            "encrypted": False if optical else is_encrypted(device),
        })
        result[-1]["healthText"] = tr(HEALTH_TEXT[result[-1]["health"]]) if result[-1]["health"] else ""

    def sort_key(d):
        if d["mountpoint"] == "/":
            return 0
        if d.get("isOptical"):
            return 1.5
        return 2 if d["isUsb"] else 1

    result.extend(lecteurs_optiques_sans_montage())
    result.sort(key=sort_key)
    return result


NETWORK_FSTYPES = {"cifs", "smb3", "smbfs", "nfs", "nfs4", "9p"}
NETWORK_FUSE_TYPES = {"fuse.sshfs", "fuse.davfs", "fuse.davfs2", "fuse.rclone"}


def _unescape_mount_path(path):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), path)


def list_network_mounts():
    mounts = []
    seen = set()
    try:
        with open("/proc/mounts", "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return mounts
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mountpoint, fstype = parts[0], parts[1], parts[2]
        mountpoint = _unescape_mount_path(mountpoint)
        if fstype not in NETWORK_FSTYPES and fstype not in NETWORK_FUSE_TYPES:
            continue
        if mountpoint in seen:
            continue
        if any(mountpoint.startswith(p) for p in IGNORED_MOUNT_PREFIXES):
            continue
        try:
            usage = shutil.disk_usage(mountpoint)
        except (PermissionError, FileNotFoundError, OSError):
            continue
        seen.add(mountpoint)
        label = os.path.basename(mountpoint.rstrip("/")) or device
        mounts.append({
            "label": label, "mountpoint": mountpoint, "device": device,
            "fstype": fstype, "total": usage.total, "used": usage.used,
            "free": usage.free,
            "icon": disk_icon(["folder-network", "network-server",
                               "network-workgroup", "folder-remote"]),
            "isKey": False, "isUsb": False, "isNetwork": True,
            "freeText": tr("{free} free of {total}").format(free=taille(usage.free), total=taille(usage.total)),
        })
    return mounts


CONFIG_DIR = os.path.expanduser("~/.config/fenestra")
NET_CONFIG = os.path.join(CONFIG_DIR, "ce-pc-reseau.json")


def load_net_locations():
    try:
        with open(NET_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        return [d for d in data if isinstance(d, dict) and d.get("host") and d.get("share")]
    except Exception:
        return []


def save_net_locations(locs):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = NET_CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(locs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, NET_CONFIG)


def net_url(loc):
    from urllib.parse import quote
    user = (loc.get("user") or "").strip()
    return ("smb://" + (quote(user, safe="") + "@" if user else "")
            + loc["host"] + "/" + quote(loc["share"]))


PLACES = os.path.expanduser("~/.local/share/user-places.xbel")
PLACES_TAG = "fenestra-cepc-"


def sync_places(locs):
    """Remet les favoris « Distant » de Dolphin en phase avec la liste des emplacements réseau de Ce PC.
    Ne touche qu'aux favoris créés par Ce PC (identifiant « fenestra-cepc-… »)."""
    try:
        with open(PLACES, encoding="utf-8") as f:
            texte = f.read()
    except OSError:
        return
    if "</xbel>" not in texte or "xmlns:bookmark=" not in texte:
        return
    from xml.sax.saxutils import escape

    def enlever(m):
        return "" if PLACES_TAG in m.group(0) else m.group(0)

    base = re.sub(r'[ \t]*<bookmark href="[^"]*">(?:(?!</bookmark>).)*</bookmark>[ \t]*\n?',
                  enlever, texte, flags=re.S)
    blocs = ""
    for loc in locs:
        url = net_url(loc)
        titre = nom_emplacement(loc)
        ident = PLACES_TAG + hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
        blocs += (' <bookmark href="' + escape(url, {'"': "&quot;"}) + '">\n'
                  '  <title>' + escape(titre) + '</title>\n'
                  '  <info>\n'
                  '   <metadata owner="http://freedesktop.org">\n'
                  '    <bookmark:icon name="folder-network"/>\n'
                  '   </metadata>\n'
                  '   <metadata owner="http://www.kde.org">\n'
                  '    <ID>' + ident + '</ID>\n'
                  '    <isSystemItem>false</isSystemItem>\n'
                  '   </metadata>\n'
                  '  </info>\n'
                  ' </bookmark>\n')
    debut, _, fin = base.rpartition("</xbel>")
    nouveau = debut + blocs + "</xbel>" + fin
    if nouveau == texte:
        return
    try:
        bak = PLACES + ".bak-fenestra"
        if not os.path.exists(bak):
            shutil.copy2(PLACES, bak)
        tmp = PLACES + ".fenestra.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(nouveau)
        os.replace(tmp, PLACES)
    except OSError as e:
        print("[ce_pc_plasma] favoris Dolphin :", e)


WALLET_DOSSIER = "Fenestra Ce PC"
_ESPACE = {}          # url -> (time, value or None): avoids querying the NAS every 3 seconds
_PORTEFEUILLE = {"ko_jusqu": 0}


def wallet_cle(loc):
    return net_url(loc)


def wallet_ecrire(cle, mot_de_passe):
    """Range le secret dans KWallet (lu sur l'entrée standard : rien dans la ligne de commande)."""
    try:
        r = subprocess.run(["kwallet-query", "-f", WALLET_DOSSIER, "-w", cle, "kdewallet"],
                           input=mot_de_passe, text=True, capture_output=True, timeout=60)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def wallet_lire(cle):
    if time.time() < _PORTEFEUILLE["ko_jusqu"]:
        return None
    try:
        r = subprocess.run(["kwallet-query", "-f", WALLET_DOSSIER, "-r", cle, "kdewallet"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        _PORTEFEUILLE["ko_jusqu"] = time.time() + 300
        return None
    if r.returncode != 0:
        _PORTEFEUILLE["ko_jusqu"] = time.time() + 300
        return None
    secret = r.stdout.rstrip("\n")
    return secret or None


def net_espace(loc):
    """(total, utilisé, libre) en octets d'un partage SMB, ou None. Mis en cache 2 minutes (5 en cas d'échec)."""
    url = net_url(loc)
    maintenant = time.time()
    if url in _ESPACE and maintenant < _ESPACE[url][0]:
        return _ESPACE[url][1]
    valeur = None
    mdp = wallet_lire(wallet_cle(loc)) if loc.get("user") else None
    if mdp:
        env = dict(os.environ)
        env["PASSWD"] = mdp
        try:
            r = subprocess.run(["smbclient", "//" + loc["host"] + "/" + loc["share"], "-U", loc["user"],
                                "-c", "ls"], env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=30)
            m = re.search(r"(\d+) blocks of size (\d+)\. (\d+) blocks available", r.stdout or "")
            if m:
                taille_bloc = int(m.group(2))
                total = int(m.group(1)) * taille_bloc
                libre = int(m.group(3)) * taille_bloc
                if total > 0:
                    valeur = (total, max(total - libre, 0), libre)
        except (OSError, subprocess.SubprocessError):
            valeur = None
        finally:
            env.pop("PASSWD", None)
    _ESPACE[url] = (maintenant + (120 if valeur else 300), valeur)
    return valeur


def badge_health():
    """Pastilles de santé du disque (santeDisque) : orange = signes d'usure, rouge = défaillant. {état: adresse}."""
    res = {}
    for etat, couleur in (("warn", "#f7630c"), ("fail", "#c42b1c")):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20">\n'
               '  <circle cx="10" cy="10" r="10" fill="%s"/>\n'
               '  <path d="M10 4.6 L10 11.4" stroke="#ffffff" stroke-width="2.6" stroke-linecap="round"/>\n'
               '  <circle cx="10" cy="15" r="1.5" fill="#ffffff"/>\n'
               '</svg>\n' % couleur)
        chemin = os.path.join(os.path.expanduser("~/.cache/fenestra"), "ce-pc-sante-%s.svg" % etat)
        try:
            os.makedirs(os.path.dirname(chemin), exist_ok=True)
            if not os.path.isfile(chemin) or open(chemin, encoding="utf-8").read() != svg:
                with open(chemin, "w", encoding="utf-8") as f:
                    f.write(svg)
            res[etat] = "file://" + chemin
        except OSError:
            res[etat] = ""
    return res


def badge_offline():
    """Crée (si besoin) l'image SVG de la pastille « non connecté » et renvoie son adresse file://."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20">\n'
           '  <circle cx="10" cy="10" r="10" fill="#da4453"/>\n'
           '  <path d="M5.6 5.6 L14.4 14.4 M14.4 5.6 L5.6 14.4" stroke="#ffffff" stroke-width="2.6" '
           'stroke-linecap="round" fill="none"/>\n'
           '</svg>\n')
    chemin = os.path.join(os.path.expanduser("~/.cache/fenestra"), "ce-pc-pastille.svg")
    try:
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        if not os.path.isfile(chemin) or open(chemin, encoding="utf-8").read() != svg:
            with open(chemin, "w", encoding="utf-8") as f:
                f.write(svg)
        return "file://" + chemin
    except OSError:
        return ""


SECT_CONFIG = os.path.join(CONFIG_DIR, "ce-pc-rubriques.json")   # rubriquesMemo (This PC 1.1)
OPTIONS_CONFIG = os.path.join(CONFIG_DIR, "ce-pc-options.json")   # options (2.0): alert threshold
KDEGLOBALS = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "kdeglobals")


def lire_simple_clic():
    """Réglage de Plasma « Cliquer sur les fichiers ou les dossiers : les ouvrir » ([KDE] SingleClick)."""
    c = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        c.read(KDEGLOBALS, encoding="utf-8")
        return c.get("KDE", "SingleClick", fallback="false").strip().lower() == "true"
    except (configparser.Error, OSError, UnicodeDecodeError):
        return False


def lire_compact():
    """Barre d'outils compacte (icônes seules) : faux par défaut."""
    try:
        return bool(json.load(open(OPTIONS_CONFIG, encoding="utf-8")).get("compact", False))
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def lire_seuil():
    """Seuil d'alerte d'espace libre en % (10 à 20, 20 par défaut)."""
    try:
        return max(10, min(20, int(json.load(open(OPTIONS_CONFIG, encoding="utf-8")).get("seuil", 20))))
    except (OSError, ValueError, TypeError, AttributeError):
        return 20
SECTIONS = ("folders", "disks", "net", "printers")


def load_sections():
    """Rubriques : repliée ou non, affichée ou non. Par défaut, tout est affiché et déplié."""
    res = {k: {"collapsed": False, "shown": True} for k in SECTIONS}
    try:
        with open(SECT_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        for k in SECTIONS:
            d = data.get(k, {})
            res[k]["collapsed"] = bool(d.get("collapsed", False))
            res[k]["shown"] = bool(d.get("shown", True))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return res


def host_reachable(host, port=445, timeout=0.8):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def build_network_list():
    """Partages montés + emplacements ajoutés par l'utilisateur (avec leur disponibilité).
    Appelée dans un thread : un serveur qui ne répond pas ne doit pas figer la fenêtre."""
    mounts = list_network_mounts()
    result = []
    used = set()
    for loc in load_net_locations():
        key = ("//%s/%s" % (loc["host"], loc["share"])).lower()
        label = nom_emplacement(loc)
        url = net_url(loc)
        m = next((x for x in mounts if x["device"].lower().rstrip("/") == key), None)
        if m:
            used.add(m["mountpoint"])
            entry = dict(m)
            entry.update({"label": label, "available": True, "mounted": True,
                          "configured": True, "url": url})
        else:
            ok = host_reachable(loc["host"])
            esp = net_espace(loc) if (ok and loc.get("retenir")) else None
            entry = {
                "label": label, "mountpoint": url, "device": "//%s/%s" % (loc["host"], loc["share"]),
                "fstype": "smb", "total": esp[0] if esp else 0, "used": esp[1] if esp else 0,
                "free": esp[2] if esp else 0,
                "icon": disk_icon(["folder-network", "network-server",
                                   "network-workgroup", "folder-remote"]),
                "isKey": False, "isUsb": False, "isNetwork": True,
                "freeText": (tr("{free} free of {total}").format(free=taille(esp[2]), total=taille(esp[0])) if esp
                             else (tr("Available") if ok else tr("Not connected"))),
                "available": ok, "mounted": False, "configured": True, "url": url,
            }
        result.append(entry)
    for m in mounts:
        if m["mountpoint"] not in used:
            entry = dict(m)
            entry.update({"available": True, "mounted": True, "configured": False,
                          "url": ""})
            result.append(entry)
    return result


NET_CACHE = os.path.expanduser("~/.cache/fenestra/ce-pc-reseau-cache.json")   # reseauCache


def charger_cache_reseau():
    """Dernier état connu des emplacements réseau, pour les afficher sans attendre (liste vide si absent)."""
    try:
        with open(NET_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and all(isinstance(x, dict) for x in data):
            return data
    except (OSError, ValueError):
        pass
    return []


_RESEAU_ECRIT = None   # cachesEcriture: last content written, to rewrite only when the list changes


def enregistrer_cache_reseau(liste):
    global _RESEAU_ECRIT
    try:
        contenu = json.dumps(liste)
        if contenu == _RESEAU_ECRIT:
            return
        os.makedirs(os.path.dirname(NET_CACHE), exist_ok=True)
        tmp = NET_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(contenu)
        os.replace(tmp, NET_CACHE)
        _RESEAU_ECRIT = contenu
    except (OSError, TypeError, ValueError):
        pass


PRN_CACHE = os.path.expanduser("~/.cache/fenestra/ce-pc-imprimantes-cache.json")   # imprimantesCache


def charger_cache_imprimantes():
    """Dernier état connu des imprimantes, pour les afficher sans attendre (liste vide si absent ou invalide)."""
    try:
        with open(PRN_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and all(isinstance(x, dict) and "key" in x and "label" in x for x in data):
            return data
    except (OSError, ValueError):
        pass
    return []


_PRN_ECRIT = None   # cachesEcriture


def enregistrer_cache_imprimantes(liste):
    global _PRN_ECRIT
    try:
        contenu = json.dumps(liste)
        if contenu == _PRN_ECRIT:
            return
        os.makedirs(os.path.dirname(PRN_CACHE), exist_ok=True)
        tmp = PRN_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(contenu)
        os.replace(tmp, PRN_CACHE)
        _PRN_ECRIT = contenu
    except (OSError, TypeError, ValueError):
        pass


IPPTOOL_TEST = "/usr/share/cups/ipptool/get-printer-attributes.test"   # imprimantesEncre


def libelle_connexion(uri):
    """Connexion lisible : « détectée automatiquement » pour une imprimante trouvée sur le réseau local."""
    if uri.startswith("implicitclass:") or "._ipp" in uri or "._print" in uri:
        return tr("Detected automatically on the network")
    if uri.startswith("usb:"):   # imprimantesUsb: without the serial number
        return tr("Connected via USB")
    return uri


_ENCRE_CACHE = {}


def niveaux_encre(nom, cible=None):
    """Niveaux d'encre : d'abord ceux que CUPS connaît, sinon ceux que l'imprimante donne directement. Gardés 20 s."""   # encreDirecte
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", nom):
        return []
    memo = _ENCRE_CACHE.get(nom)
    if memo and time.time() - memo[0] < 20:
        return memo[1]
    res = _encre_url("ipp://localhost/printers/" + nom)
    if not res and cible and cible[1] == 631:
        res = _encre_url("ipp://%s:%d/ipp/print" % (cible[0], cible[1]))
    _ENCRE_CACHE[nom] = (time.time(), res)
    return res


def _encre_url(url):
    """Niveaux d'encre lus à cette adresse IPP : [{name, color, level, low}]. Liste vide si inconnus ou si ipptool est absent."""
    if not os.path.isfile(IPPTOOL_TEST):
        return []
    env = dict(os.environ, LC_ALL="C", LANG="C", LANGUAGE="C")
    try:
        out = subprocess.run(["ipptool", "-tv", url, IPPTOOL_TEST],
                             capture_output=True, text=True, timeout=5, env=env).stdout
    except Exception:
        return []

    def champ(cle):
        m = re.search(r"^\s*" + cle + r" \([^)]*\) = (.*)$", out, re.M)
        return [x.strip() for x in m.group(1).split(",")] if m else []

    noms, niveaux, couleurs = champ("marker-names"), champ("marker-levels"), champ("marker-colors")
    bas, types = champ("marker-low-levels"), champ("marker-types")
    res = []
    for i, n in enumerate(noms):
        try:
            niveau = int(niveaux[i])
        except (IndexError, ValueError):
            continue
        if niveau < 0 or niveau > 100 or (i < len(types) and "waste" in types[i]):
            continue
        try:
            seuil = int(bas[i])
        except (IndexError, ValueError):
            seuil = 10   # seuilEncreDefaut: the printer declares no "low" threshold: alert at 10 %
        couleur = couleurs[i] if i < len(couleurs) and re.fullmatch(r"#[0-9A-Fa-f]{6}", couleurs[i]) else "#808080"
        res.append({"name": nom_encre(n), "color": couleur, "level": niveau, "low": niveau <= seuil})
    return res


# encresTraduites (2.0): CUPS gives the names in English ("Photo Black ink"); known colours are translated
_ENCRES = (("photo black", "Photo black"), ("matte black", "Matte black"), ("light cyan", "Light cyan"),
           ("light magenta", "Light magenta"), ("black", "Black"), ("cyan", "Cyan"), ("magenta", "Magenta"),
           ("yellow", "Yellow"), ("gray", "Gray"), ("grey", "Gray"))


def nom_encre(nom):
    bas = nom.lower()
    for cle, texte in _ENCRES:
        if cle in bas:
            return tr(texte)
    return nom


PRN_CONNUES = os.path.expanduser("~/.config/fenestra/ce-pc-imprimantes.json")   # imprimantesConnues
_PRN_CONNUES_ECRIT = [None]
_PRN_ESSAI = {}


def charger_imprimantes_connues():
    """Imprimantes déjà vues : {nom de file: {label, device, default, addr, port}} (vide si absent ou invalide)."""
    try:
        with open(PRN_CONNUES, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and all(isinstance(v, dict) for v in data.values()):
            return data
    except (OSError, ValueError):
        pass
    return {}


def enregistrer_imprimantes_connues(donnees):
    contenu = json.dumps(donnees, sort_keys=True)
    if contenu == _PRN_CONNUES_ECRIT[0]:
        return
    try:
        os.makedirs(os.path.dirname(PRN_CONNUES), exist_ok=True)
        tmp = PRN_CONNUES + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(contenu)
        os.replace(tmp, PRN_CONNUES)
        _PRN_CONNUES_ECRIT[0] = contenu
    except OSError:
        pass


def oublier_imprimante(nom):
    donnees = charger_imprimantes_connues()
    if nom in donnees:
        del donnees[nom]
        enregistrer_imprimantes_connues(donnees)


def cible_uri(uri):
    """(hôte, port) lu dans l'adresse d'une file réseau classique, sinon None (USB, file automatique, etc.)."""
    m = re.match(r"^(ipps?|https?|socket|lpd)://([^/:@]+)(?::(\d+))?", uri)
    if not m:
        return None
    defaut = {"ipp": 631, "ipps": 631, "http": 80, "https": 443, "socket": 9100, "lpd": 515}[m.group(1)]
    return m.group(2), int(m.group(3) or defaut)


def cle_service(uri):   # imprimantesDnssd
    """Nom d'annonce lu dans une adresse dnssd://… (sous la forme comparable), sinon None."""
    m = re.match(r"^dnssd://(.+?)\._(?:ipps?|printer)\._tcp\.local\.?/", uri)
    if not m:
        return None
    from urllib.parse import unquote
    return re.sub(r"[^A-Za-z0-9]+", "_", unquote(m.group(1)))


def adresse_avahi(nom, avait_adresse, cle=None):
    """(adresse IPv4, port) de l'imprimante annoncée sur le réseau sous ce nom de file, ou None.
    Au plus un essai toutes les 30 s (120 s si on connaissait déjà une adresse), car la recherche peut durer."""
    maintenant = time.time()
    if maintenant - _PRN_ESSAI.get(nom, 0) < (120 if avait_adresse else 30):
        return None
    _PRN_ESSAI[nom] = maintenant
    try:
        out = subprocess.run(["avahi-browse", "-rpt", "_ipp._tcp"], capture_output=True, text=True,
                             timeout=8).stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
    except Exception:
        return None
    for ligne in out.splitlines():
        p = ligne.split(";")
        if len(p) < 9 or p[0] != "=" or p[2] != "IPv4":
            continue
        service = re.sub(r"\\(\d{3})", lambda m: chr(int(m.group(1))), p[3])
        if re.sub(r"[^A-Za-z0-9]+", "_", service) == (cle or nom):
            try:
                return p[7], int(p[8])
            except ValueError:
                continue
    return None


def imprimante_repond(adresse, port):
    try:
        with socket.create_connection((adresse, port), timeout=1.5):
            return True
    except OSError:
        return False


# imprimanteMicrocoupure (2.1): a single failed test (Wi-Fi printer waking up, busy network) no longer
# greys out the card; ECHECS_MAX failed tests in a row are needed (one test every 3 s)
ECHECS_MAX = 3
_ECHECS = {}   # printer name -> number of failed tests in a row


def _ipv4(hote):   # imprimanteAdresseIp (2.1): address shown only if it really is an IPv4 address
    hote = hote or ""
    return hote if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", hote) else ""


def list_printers():
    """Imprimantes CUPS : [{key, label, name, icon, statusText, device}]. Liste vide si aucune ou si CUPS est absent."""   # imprimantesInfo
    env = dict(os.environ, LC_ALL="C", LANG="C", LANGUAGE="C")
    try:
        out = subprocess.run(["lpstat", "-p", "-d", "-v"], capture_output=True, text=True,
                             timeout=5, env=env).stdout
    except Exception:
        return []
    defaut = ""
    uris = {}
    etats = {}
    ordre = []
    for ligne in out.splitlines():
        m = re.match(r"^system default destination: (\S+)", ligne)
        if m:
            defaut = m.group(1)
            continue
        m = re.match(r"^device for (\S+): (.*)$", ligne)
        if m:
            uris[m.group(1)] = re.sub(r"//[^/@]*@", "//", m.group(2).strip())
            continue
        m = re.match(r"^printer (\S+) (.*)$", ligne)
        if m:
            nom, reste = m.group(1), m.group(2)
            if "disabled" in reste:
                etat = tr("Offline")
            elif "now printing" in reste:
                etat = tr("Printing")
            else:
                etat = tr("Ready")
            etats[nom] = etat
            ordre.append(nom)
    connues = charger_imprimantes_connues()
    res = []
    vus = set()
    for nom in ordre:
        vus.add(nom)
        uri = uris.get(nom, "")
        fiche = dict(connues.get(nom, {}))
        injoignable = False
        cible = None
        if not uri.startswith("usb:"):
            cible = cible_uri(uri)
            if cible is None and fiche.get("addr"):
                cible = (fiche["addr"], fiche.get("port", 631))
            ok = None
            if cible is not None:
                ok = imprimante_repond(*cible)
            if (ok is not True) and cible_uri(uri) is None:
                trouvee = adresse_avahi(nom, cible is not None, cle_service(uri))
                if trouvee:
                    cible = trouvee
                    ok = imprimante_repond(*cible)
            if cible is not None and cible_uri(uri) is None:
                fiche["addr"], fiche["port"] = cible
            if cible is not None:
                _ECHECS[nom] = 0 if ok else _ECHECS.get(nom, 0) + 1
            injoignable = (cible is not None and ok is False and _ECHECS.get(nom, 0) >= ECHECS_MAX)
        etat = tr("Offline") if injoignable else etats[nom]
        texte = etat + ((" · " + tr("Default")) if nom == defaut else "")
        peripherique = libelle_connexion(uri)
        fiche.update({"label": nom.replace("_", " "), "device": peripherique, "default": nom == defaut})
        connues[nom] = fiche
        res.append({"key": "printer:" + nom, "label": nom.replace("_", " "), "name": nom,
                    "icon": disk_icon(["printer"]), "statusText": texte,
                    "device": peripherique, "inks": [] if injoignable else niveaux_encre(nom, cible),
                    "offline": injoignable, "absent": False, "addr": _ipv4(cible[0] if cible else "")})
    for nom, fiche in list(connues.items()):
        if nom in vus:
            continue
        texte = tr("Offline") + ((" · " + tr("Default")) if fiche.get("default") else "")
        res.append({"key": "printer:" + nom, "label": fiche.get("label") or nom.replace("_", " "), "name": nom,
                    "icon": disk_icon(["printer"]), "statusText": texte,
                    "device": fiche.get("device", ""), "inks": [], "offline": True, "absent": True,
                    "addr": _ipv4(fiche.get("addr", ""))})
    enregistrer_imprimantes_connues(connues)
    return res


_PERIPH_ETAT = [None, []]   # peripheriquesInfo: last list read (read again only if /proc/bus/input/devices changes)
_PERIPH_TYPES = [("ID_INPUT_TOUCHPAD", "touchpad", "Touchpad", "input-touchpad"),
                 ("ID_INPUT_TABLET", "tablet", "Graphics tablet", "input-tablet"),
                 ("ID_INPUT_KEYBOARD", "keyboard", "Keyboard", "input-keyboard"),
                 ("ID_INPUT_MOUSE", "mouse", "Mouse", "input-mouse"),
                 ("ID_INPUT_JOYSTICK", "gamepad", "Game controller", "input-gaming")]
_PERIPH_ORDRE = {"keyboard": 0, "mouse": 1, "touchpad": 2, "tablet": 3, "gamepad": 4}


def list_peripheriques():
    """Claviers, souris, pavés tactiles, tablettes et manettes : mêmes champs que les imprimantes, plus indicator=True."""
    try:
        with open("/proc/bus/input/devices", encoding="utf-8", errors="replace") as f:
            signature = f.read()
    except OSError:
        return []
    if signature == _PERIPH_ETAT[0]:
        return _PERIPH_ETAT[1]
    try:
        out = subprocess.run(["udevadm", "info", "--export-db"], capture_output=True, text=True,
                             timeout=8).stdout
    except Exception:
        return []
    trouves = {}
    for bloc in out.split("\n\n"):
        lignes = bloc.splitlines()
        if not lignes or not re.match(r"^P: .*/input/input\d+$", lignes[0]):
            continue
        chemin = lignes[0][3:]
        if chemin.startswith("/devices/virtual/input/"):
            continue
        props = {}
        for ligne in lignes[1:]:
            if ligne.startswith("E: ") and "=" in ligne:
                cle, valeur = ligne[3:].split("=", 1)
                props[cle] = valeur.strip().strip('"')
        nom = props.get("NAME", "").strip()
        if not nom:
            continue
        if "/usb" in chemin:
            connexion = "USB"
        elif "/uhid/0005:" in chemin or "/bluetooth" in chemin:
            connexion = "Bluetooth"
        elif "/serio" in chemin or "/i8042" in chemin:
            connexion = tr("Built-in")
        else:
            connexion = ""
        m = re.search(r"(usb\d+/[\d.-]+)/", chemin)
        b = re.search(r"/uhid/(0005:[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4})\.", chemin)
        groupe = m.group(1) if m else ("bt:" + b.group(1) if b else chemin)
        for propriete, genre, texte, icone in _PERIPH_TYPES:
            if props.get(propriete) == "1":
                cle = (groupe, genre)
                if cle not in trouves or len(nom) < len(trouves[cle]["name"]):
                    trouves[cle] = {"key": "device:" + chemin, "label": nom, "name": nom,
                                    "icon": disk_icon([icone]),
                                    "statusText": tr(texte) + ((" · " + connexion) if connexion else ""),
                                    "device": "", "inks": [], "indicator": True, "_genre": genre}
                break
    # peripheriquesSouris: the "keyboard" of a mouse (name = mouse name + "Keyboard") is not a real keyboard
    for (groupe, genre), d in list(trouves.items()):
        if genre == "keyboard" and any(g == groupe and gen == "mouse" and d["name"].startswith(s["name"])
                                       for (g, gen), s in trouves.items()):
            del trouves[(groupe, genre)]
    liste = sorted(trouves.values(), key=lambda d: (_PERIPH_ORDRE[d["_genre"]], d["label"]))
    for d in liste:
        del d["_genre"]
    _PERIPH_ETAT[0], _PERIPH_ETAT[1] = signature, liste
    return liste


# scannersInfo (This PC 1.1): scanners found by SANE (scanimage -L). The network search takes ~10 s:
# it runs in the background (at startup then every 10 minutes) and the last result is kept
# in cache. Security: a NETWORK scanner is shown only if it matches a printer installed
# on this PC (same model name); other people's scanners on the same Wi-Fi stay hidden.
SCAN_CACHE = os.path.expanduser("~/.cache/fenestra/ce-pc-scanners-cache.json")
_SCAN_TYPE = re.compile(r"\s+(?:(?:flatbed|platen|sheetfed|sheet-fed|adf|film|handheld)\s+)?scanner$"
                        r"|\s+multi-function peripheral$|\s+all-in-one$", re.I)


def _norme(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def detecter_scanners():
    """[{device, model, network}] d'après scanimage -L ; None si la recherche a échoué (on garde l'ancien résultat)."""
    if not shutil.which("scanimage"):
        return []
    try:
        out = subprocess.run(["scanimage", "-L"], capture_output=True, text=True, timeout=45).stdout
    except Exception:
        return None
    trouves = []
    for ligne in out.splitlines():
        m = re.search(r"device [`'](.+?)' is an? (.+)$", ligne.strip())   # the HTML page of some printers may come first
        if not m:
            continue
        device, desc = m.group(1), m.group(2).strip()
        desc = re.sub(r"\s+ip=\S+$", "", re.sub(r"^(?:eSCL|WSD)\s+", "", desc))   # form of the airscan driver
        backend = device.split(":", 1)[0].lower()
        if backend in ("v4l", "test", "pixma_test") or desc.lower().endswith("virtual device"):
            continue   # webcams and test devices seen by SANE: not scanners
        model = _SCAN_TYPE.sub("", desc).strip() or desc
        # scannerReseau (2.0): "epson2:net:192.168.1.225" is also a network scanner (not USB)
        reseau = backend in ("escl", "airscan", "net") or "://" in device or ":net:" in device
        ip = re.search(r"\d{1,3}(?:\.\d{1,3}){3}", device)
        trouves.append((0 if backend in ("escl", "airscan") else 1, device, model, reseau, ip.group(0) if ip else ""))
    # scannerDoublon (2.0): the same device seen by several drivers (escl, airscan, epson2…) is kept
    # only once: same model or same IP address; escl/airscan first (they give the commercial name)
    res, vus, ips = [], set(), set()
    for _prio, device, model, reseau, ip in sorted(trouves, key=lambda x: x[0]):
        if _norme(model) in vus or (ip and ip in ips):
            continue
        vus.add(_norme(model))
        if ip:
            ips.add(ip)
        res.append({"device": device, "model": model, "network": reseau})
    return res


def charger_cache_scanners():
    try:
        with open(SCAN_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and all(isinstance(x, dict) and "device" in x and "model" in x for x in data):
            return data
    except (OSError, ValueError):
        pass
    return []


def enregistrer_cache_scanners(liste):
    try:
        os.makedirs(os.path.dirname(SCAN_CACHE), exist_ok=True)
        tmp = SCAN_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(liste, f)
        os.replace(tmp, SCAN_CACHE)
    except (OSError, TypeError, ValueError):
        pass


def cartes_scanners(scanners, trouves, imprimantes):
    """Cartes des scanners à afficher. scanners : dernière liste connue ; trouves : appareils vus à la
    dernière recherche (les autres sont « hors ligne ») ; imprimantes : cartes des imprimantes installées."""
    noms = {}   # comparable name -> is the printer offline?
    for d in imprimantes:
        if d.get("indicator") or d.get("scanner"):
            continue
        for s in (d.get("name"), d.get("label")):
            if _norme(s):
                noms[_norme(s)] = bool(d.get("offline"))
    try:   # network announcement name of the dnssd://… queues ("EPSON XP-540 Series")
        out = subprocess.run(["lpstat", "-v"], capture_output=True, text=True, timeout=5,
                             env=dict(os.environ, LC_ALL="C", LANG="C", LANGUAGE="C")).stdout
        for m in re.finditer(r"^device for (\S+): dnssd://([^/]+?)\._(?:ipps?|printer)\._tcp", out, re.M):
            from urllib.parse import unquote
            hors = next((v for n, v in noms.items() if n == _norme(m.group(1))), False)
            noms[_norme(unquote(m.group(2)))] = hors
    except Exception:
        pass
    res = []
    for s in scanners:
        cle = _norme(s["model"])
        lies = [hors for n, hors in noms.items() if len(n) >= 6 and (cle in n or n in cle)]
        if s.get("network") and not lies:
            continue
        # scannerVeille: a network scanner follows the state of its printer (read again every 3 s), not the scanner
        # search (slow, every 10 min): printer off = scanner "offline" right away,
        # printer asleep but "Ready" = scanner shown normally
        if s.get("network") and lies:
            present = not all(lies)
        else:
            present = s["device"] in trouves
        texte = tr("Scanner") + " · " + (tr("Network") if s.get("network") else "USB")
        if not present:
            texte += " · " + tr("Offline")
        res.append({"key": "scanner:" + s["device"], "label": s["model"], "name": s["model"],
                    "icon": disk_icon(["scanner", "document-scan", "printer"]), "statusText": texte,
                    "device": "", "inks": [], "indicator": True, "scanner": True, "offline": not present})
    return res


# miseAJour (This PC 1.1): at most once a week, This PC asks GitHub for the number of the latest
# published version. Nothing is installed: a banner only offers to open the release page.
# No personal data is sent (GitHub sees the IP address, as for any visit).
# Can be turned off in "About". Banner test: CEPC_MAJ_TEST=9.9 python3 ce_pc_plasma.py
MAJ_CONFIG = os.path.join(CONFIG_DIR, "ce-pc-mise-a-jour.json")
MAJ_API = "https://api.github.com/repos/naykkalak/fenestra-ce-pc/releases/latest"
MAJ_PAGE = "https://github.com/naykkalak/fenestra-ce-pc/releases/latest"
MAJ_INTERVALLE = 7 * 24 * 3600


STORE_REGISTRE = os.path.expanduser("~/.local/share/knewstuff3/plasmoids.knsregistry")
STORE_ID = "2377862"   # This PC's page on store.kde.org


def inscrit_dans_liste_store():
    """True si Ce PC est dans la liste des modules du KDE Store (écrite par le Store ou par install.sh) :
    Discover propose alors lui-même les mises à jour."""
    try:
        with open(STORE_REGISTRE, encoding="utf-8", errors="replace") as f:
            return "<id>%s</id>" % STORE_ID in f.read()
    except OSError:
        return False


def _version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


def charger_maj():
    etat = {"last": 0, "latest": "", "url": "", "enabled": True}
    try:
        with open(MAJ_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        etat["last"] = float(data.get("last", 0))
        etat["latest"] = str(data.get("latest", ""))
        etat["url"] = str(data.get("url", ""))
        etat["enabled"] = bool(data.get("enabled", True))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return etat


def enregistrer_maj(etat):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = MAJ_CONFIG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(etat, f)
        os.replace(tmp, MAJ_CONFIG)
    except OSError:
        pass


def lire_derniere_version():
    """(version, adresse de la page) de la dernière version publiée sur GitHub, ou None (hors ligne, erreur)."""
    import urllib.request
    try:
        req = urllib.request.Request(MAJ_API, headers={"User-Agent": "Fenestra-This-PC/" + APP_VERSION,
                                                       "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read(200000).decode("utf-8"))
        tag = str(data.get("tag_name", "")).lstrip("vV")
        url = str(data.get("html_url", ""))
        if not _version_tuple(tag) or not url.startswith("https://github.com/naykkalak/fenestra-ce-pc/"):
            return None
        return tag, url
    except Exception:
        return None


# Programs that This PC launches and that Discover can offer: Discover looks them up by name
# (the AppStream identifier varies from one distribution to another, e.g. absent from the Solus repositories)
APPS_DISCOVER = ("skanpage",)


class Backend(QObject):
    disksChanged = pyqtSignal()
    networksChanged = pyqtSignal()
    messageRequested = pyqtSignal(str, str)
    messageAppRequested = pyqtSignal(str, str, str)   # discoverApp (2.0): message + name of the program to look for in Discover
    sharesFound = pyqtSignal(list)
    sharesError = pyqtSignal(str)
    smartReady = pyqtSignal(str, str)
    healthReady = pyqtSignal(str, "QVariantMap")   # santeDetails (2.0): (device, details)
    selftestDone = pyqtSignal(str, bool)            # self-test requested: (device, accepted)
    surfaceUpdated = pyqtSignal("QVariantMap")
    usbTestUpdated = pyqtSignal(str, "QVariantMap")   # testsUsb (2.0)
    formatDone = pyqtSignal(str, bool)                # formatage (2.0): (device, succeeded)
    formatInfoReady = pyqtSignal(str, str, "QVariantMap")
    formatProgress = pyqtSignal(str, float)                  # full formatting: (device, 0 to 1)   # formatting: (device, disk name, formats)      # analyseSurface (2.0): state of the analysis
    netUpdated = pyqtSignal(list)
    printersUpdated = pyqtSignal(list)
    updateAvailable = pyqtSignal(str, str)   # miseAJour: (version, page address)
    printersChanged = pyqtSignal()
    optionsChanged = pyqtSignal()   # options (2.0): single click, alert threshold

    def __init__(self):
        super().__init__()
        self._disks = []
        self._networks = charger_cache_reseau()
        self._printers = charger_cache_imprimantes()
        self._surface = None   # analyseSurface (2.0)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refreshDisks)
        self._timer.start(3000)
        self.netUpdated.connect(self._setNetworks)
        self.printersUpdated.connect(self._setPrinters)
        self._netWake = threading.Event()
        threading.Thread(target=lambda: sync_places(load_net_locations()), daemon=True).start()
        self._scanners = charger_cache_scanners()   # scannersInfo: last known list
        self._scanTrouves = {s["device"] for s in self._scanners}
        self._scanWake = threading.Event()
        threading.Thread(target=self._scanLoop, daemon=True).start()
        self._maj = charger_maj()
        threading.Thread(target=self._verifierMaj, daemon=True).start()
        threading.Thread(target=self._netLoop, daemon=True).start()
        # options (2.0): the single click follows Plasma live (kdeglobals is watched: Plasma
        # replaces it entirely at each change, hence the watching of the folder)
        self._simpleClic = lire_simple_clic()
        self._seuil = lire_seuil()
        self._compact = lire_compact()   # barreCompacte (option)
        self._veille = QFileSystemWatcher([os.path.dirname(KDEGLOBALS)], self)
        self._veille.directoryChanged.connect(lambda _: self._relireSimpleClic())
        self.refreshDisks()

    @pyqtProperty("QVariantList", notify=disksChanged)
    def disks(self):
        return self._disks

    @pyqtProperty("QVariantList", notify=networksChanged)
    def networks(self):
        return self._networks

    @pyqtProperty("QVariantList", notify=printersChanged)
    def printers(self):
        return self._printers

    def refreshDisks(self):
        new = list_real_disks()
        # formatage (2.0): a volume being formatted is unmounted; its card is kept on screen
        presents = {d["device"] for d in new}
        for dev, carte in list(getattr(self, "_formatEnCours", {}).items()):
            if dev not in presents:
                new.append(dict(carte, freeText=tr("Formatting…")))
        if new != self._disks:
            self._disks = new
            self.disksChanged.emit()

    def _netLoop(self):
        while True:
            try:
                liste = build_network_list()
                enregistrer_cache_reseau(liste)
                self.netUpdated.emit(liste)
                imprimantes = list_printers()
                imprimantes = imprimantes + cartes_scanners(self._scanners, self._scanTrouves, imprimantes) \
                    + list_peripheriques()
                enregistrer_cache_imprimantes(imprimantes)
                self.printersUpdated.emit(imprimantes)
            except Exception as e:
                print("[ce_pc_plasma] réseau :", e)
            self._netWake.wait(3)
            self._netWake.clear()

    def _verifierMaj(self):
        # miseAJour: warns of a newer version (already known, or read from GitHub at most once a week)
        time.sleep(1.5)   # lets the window show up first
        if not self._maj["enabled"]:   # box unchecked in "About": no check and no banner (test included)
            return
        test = os.environ.get("CEPC_MAJ_TEST", "")
        if test:
            self.updateAvailable.emit(test, MAJ_PAGE)
            return
        if inscrit_dans_liste_store():   # Discover already follows this component and warns about updates: no banner
            return
        if time.time() - self._maj["last"] >= MAJ_INTERVALLE:
            lu = lire_derniere_version()
            if lu:
                self._maj.update({"last": time.time(), "latest": lu[0], "url": lu[1]})
                enregistrer_maj(self._maj)
        if _version_tuple(self._maj["latest"]) > _version_tuple(APP_VERSION):
            self.updateAvailable.emit(self._maj["latest"], self._maj["url"] or MAJ_PAGE)

    @pyqtProperty(bool, constant=True)
    def storeListed(self):   # This PC is in the KDE Store list: Discover offers the updates
        return inscrit_dans_liste_store()

    @pyqtProperty(bool, constant=True)
    def updateCheck(self):
        return self._maj["enabled"]

    @pyqtSlot(bool)
    def setUpdateCheck(self, actif):
        self._maj["enabled"] = bool(actif)
        enregistrer_maj(self._maj)

    def _scanLoop(self):
        # scannersInfo: slow search (~10 s), at startup then every 10 minutes or on "Refresh".
        # scannerVeille: a sleeping printer does not always answer; as long as a printer is
        # installed and no scanner has answered, we try again every minute (10 times at most).
        essais_rapides = 0
        while True:
            delai = 600
            try:
                trouves = detecter_scanners()
                imprimante = any(not d.get("indicator") and not d.get("scanner") for d in self._printers)
                if trouves == [] and imprimante and essais_rapides < 10:
                    essais_rapides += 1
                    delai = 60
                elif trouves:
                    essais_rapides = 0
                if trouves is not None:
                    # stored by model: a network scanner that changes address (DHCP) does not make a duplicate
                    connus = {_norme(s["model"]): s for s in self._scanners}
                    for s in trouves:
                        connus[_norme(s["model"])] = s
                    vus = {s["device"] for s in trouves}
                    # a scanner that has disappeared stays shown "offline", like a switched-off printer
                    self._scanners = [s for s in connus.values() if s["device"] in vus or s.get("network")]
                    self._scanTrouves = vus
                    enregistrer_cache_scanners(self._scanners)
                    self._netWake.set()
            except Exception as e:
                print("[ce_pc_plasma] scanners :", e)
            if self._scanWake.wait(delai):
                essais_rapides = 0   # "Refresh": we start over for a series of quick attempts
            self._scanWake.clear()

    def _setNetworks(self, new_net):
        if new_net != self._networks:
            self._networks = new_net
            self.networksChanged.emit()

    def _setPrinters(self, new_list):
        if new_list != self._printers:
            self._printers = new_list
            self.printersChanged.emit()

    @pyqtSlot(str)
    def forgetPrinter(self, name):   # imprimantesConnues
        oublier_imprimante(name)
        self._netWake.set()

    @pyqtSlot(str)
    def saveSections(self, texte):
        try:
            data = json.loads(texte)
            etat = {k: {"collapsed": bool(data[k]["collapsed"]), "shown": bool(data[k]["shown"])}
                    for k in SECTIONS}
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = SECT_CONFIG + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(etat, f)
            os.replace(tmp, SECT_CONFIG)
        except (OSError, ValueError, TypeError, KeyError) as e:
            print("[ce_pc_plasma] rubriques :", e)

    @pyqtSlot(int, int, bool)
    def saveWindow(self, w, h, maximized):
        # Remembers the size. Window maximized: the last known normal size is kept.
        etat = load_window_state()
        if not maximized and 640 <= w <= 10000 and 460 <= h <= 10000:
            etat["w"], etat["h"] = int(w), int(h)
        etat["max"] = bool(maximized)
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = WIN_CONFIG + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(etat, f)
            os.replace(tmp, WIN_CONFIG)
        except OSError as e:
            print("[ce_pc_plasma] taille de la fenêtre :", e)

    @pyqtSlot(str)
    def launchApp(self, name):
        # Only these programs can be launched (toolbar, and Skanpage from a scanner's card)
        if name not in ("systemsettings", "kinfocenter", "skanpage"):
            return
        try:
            subprocess.Popen([name])
        except OSError:
            titre = tr("Program not found")
            texte = tr("The program “{name}” is not installed on this computer.").format(name=name)
            # discoverApp (2.0): if Discover is there, the box offers to look for the program in it
            if name in APPS_DISCOVER and shutil.which("plasma-discover"):
                self.messageAppRequested.emit(titre, texte, name)
            else:
                self.messageRequested.emit(titre, texte)

    @pyqtSlot(str)
    def openDiscover(self, name):
        # Opens Discover on a search for this program, without installing anything: the user stays in control.
        if name not in APPS_DISCOVER:
            return
        try:
            subprocess.Popen(["plasma-discover", "--search", name], start_new_session=True)
        except OSError:
            self.messageRequested.emit(tr("Program not found"),
                                       tr("The program “{name}” is not installed on this computer.").format(name="plasma-discover"))

    @pyqtSlot()
    def refreshAll(self):
        self.refreshDisks()
        self._netWake.set()
        self._scanWake.set()

    @pyqtSlot(str, str, str)
    def listShares(self, host, user, password):
        # Lists a server's shares with smbclient (in the background). The password goes through
        # the process environment, never through the command line, and is not kept.
        host, user = host.strip(), user.strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", host):
            self.sharesError.emit(tr("The server address is empty or contains a forbidden character."))
            return
        if user and not re.fullmatch(r"[^/\\@:\s]+", user):
            self.sharesError.emit(tr("The username contains a forbidden character."))
            return
        threading.Thread(target=self._listSharesWork, args=(host, user, password), daemon=True).start()

    def _listSharesWork(self, host, user, password):
        env = dict(os.environ)
        cmd = ["smbclient", "-g", "-L", "//" + host]
        if user:
            cmd += ["-U", user]
            env["PASSWD"] = password
        else:
            cmd += ["-N"]
        try:
            r = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=20)
        except FileNotFoundError:
            self.sharesError.emit(tr("The smbclient program is not installed (package “samba”). "
                                     "Type the share name by hand, or use the “Open” button after adding it."))
            return
        except subprocess.TimeoutExpired:
            self.sharesError.emit(tr("The server is not responding (timed out)."))
            return
        finally:
            env.pop("PASSWD", None)
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        shares = []
        for ligne in (r.stdout or "").splitlines():
            parts = ligne.split("|")
            if len(parts) >= 2 and parts[0] == "Disk" and parts[1] and not parts[1].endswith("$"):
                shares.append(parts[1])
        if shares:
            self.sharesFound.emit(shares)
            return
        if "LOGON_FAILURE" in out or "ACCESS_DENIED" in out:
            self.sharesError.emit(tr("Incorrect username or password."))
        elif "CONNECTION_REFUSED" in out or "UNREACHABLE" in out or "Connection to" in out:
            self.sharesError.emit(tr("Server unreachable. Check the address and that the server is turned on."))
        elif r.returncode == 0:
            self.sharesError.emit(tr("No share visible with these credentials."))
        else:
            self.sharesError.emit(tr("The search failed."))

    @pyqtSlot(str, str, str, str, str, bool)
    def addNetwork(self, name, host, share, user, password, remember):
        titre = tr("Cannot add")
        name, host, share, user = name.strip(), host.strip(), share.strip().strip("/"), user.strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", host):
            self.messageRequested.emit(titre, tr("The server address is empty or contains a forbidden character."))
            return
        if not share or any(c in share for c in "\\/") or len(share) > 80:
            self.messageRequested.emit(titre, tr("The share name is empty or contains a forbidden character."))
            return
        if not user:
            self.messageRequested.emit(titre, tr("The username is required."))
            return
        if user and not re.fullmatch(r"[^/\\@:\s]+", user):
            self.messageRequested.emit(titre, tr("The username contains a forbidden character."))
            return
        locs = load_net_locations()
        if any(l["host"].lower() == host.lower() and l["share"].lower() == share.lower() for l in locs):
            self.messageRequested.emit(titre, tr("This location already exists in the list."))
            return
        if remember and not password:
            self.messageRequested.emit(titre, tr("Type the password to be able to remember it."))
            return
        if remember and not user:
            self.messageRequested.emit(titre, tr("The username is required to remember the password."))
            return
        nouveau = {"name": name, "host": host,
                   "share": share, "user": user}
        if remember:
            nouveau["retenir"] = True
        locs.append(nouveau)
        try:
            save_net_locations(locs)
        except OSError as e:
            self.messageRequested.emit(titre, tr("Could not save the list:\n{error}").format(error=e))
            return
        sync_places(locs)
        if remember:
            def _ranger():
                if not wallet_ecrire(wallet_cle(nouveau), password):
                    self.messageRequested.emit(tr("Password not remembered"),
                                               tr("KWallet could not save the password: "
                                                  "the gauge will not be shown."))
                _ESPACE.pop(net_url(nouveau), None)
                _PORTEFEUILLE["ko_jusqu"] = 0
                self._netWake.set()
            threading.Thread(target=_ranger, daemon=True).start()
        self._netWake.set()

    @pyqtSlot(str)
    def removeNetwork(self, url):
        retirees = [l for l in load_net_locations() if net_url(l) == url and l.get("retenir")]
        locs = [l for l in load_net_locations() if net_url(l) != url]
        try:
            save_net_locations(locs)
        except OSError as e:
            self.messageRequested.emit(tr("Cannot remove"), str(e))
            return
        for l in retirees:
            threading.Thread(target=wallet_ecrire, args=(wallet_cle(l), ""), daemon=True).start()
        sync_places(locs)
        self._netWake.set()

    @pyqtSlot(str)
    def ejectOptical(self, device):
        threading.Thread(target=self._ejectOptical, args=(device,), daemon=True).start()

    def _ejectOptical(self, device):
        try:
            r = subprocess.run(["eject", device], capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            self.messageRequested.emit(tr("Cannot eject"), tr("Program not found"))
            return
        except subprocess.TimeoutExpired:
            self.messageRequested.emit(tr("Cannot eject"), tr("unknown error"))
            return
        if r.returncode != 0:
            self.messageRequested.emit(tr("Cannot eject"), (r.stderr or "").strip() or tr("unknown error"))

    def _openOptical(self, device):
        # Disk present but not mounted: we mount it, then open it in Dolphin
        if not disque_present(device):
            return
        try:
            r = subprocess.run(["udisksctl", "mount", "-b", device],
                               capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            self.messageRequested.emit(tr("Cannot open"), tr("Program not found"))
            return
        except subprocess.TimeoutExpired:
            self.messageRequested.emit(tr("Cannot open"), tr("unknown error"))
            return
        m = re.search(r" at (.+?)\.?\s*$", (r.stdout or "").strip())
        if r.returncode != 0 or not m:
            detail = ((r.stderr or "") + (r.stdout or "")).strip() or tr("unknown error")
            self.messageRequested.emit(tr("Cannot open"), detail)
            return
        self.openFolder(m.group(1))

    @pyqtSlot(str, str)
    def ejectDevice(self, device, label):
        threading.Thread(target=self._eject, args=(device, label),
                         daemon=True).start()

    def _eject(self, device, label):
        titre = tr("Cannot eject")
        base_disque = get_base_disk_device(device)   # before locking: afterwards the encrypted volume no longer exists
        try:
            result = subprocess.run(
                ["udisksctl", "unmount", "-b", device],
                capture_output=True, text=True, timeout=15)
        except FileNotFoundError:
            self.messageRequested.emit(
                titre, tr("The udisksctl command was not found on this system."))
            return
        except subprocess.TimeoutExpired:
            self.messageRequested.emit(
                titre, tr("Unmounting {label} timed out.").format(label=label))
            return
        if result.returncode != 0:
            detail = result.stderr.strip() or tr("unknown error")
            self.messageRequested.emit(
                titre,
                tr("Could not unmount {label}:\n{detail}\n\n"
                   "Check that no file is still open on this drive.").format(label=label, detail=detail))
            return
        dessous = _sous_jacent(device)
        if dessous != device:   # encrypted disk: the container is locked before power-off
            try:
                result = subprocess.run(["udisksctl", "lock", "-b", dessous],
                                        capture_output=True, text=True, timeout=15)
                if result.returncode != 0:
                    self.messageRequested.emit(
                        titre,
                        tr("Could not lock {label}:\n{detail}").format(
                            label=label, detail=result.stderr.strip() or tr("unknown error")))
                    return
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return
        try:
            result = subprocess.run(
                ["udisksctl", "power-off", "-b", base_disque],
                capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                detail = result.stderr.strip() or tr("unknown error")
                self.messageRequested.emit(
                    tr("Partial eject"),
                    tr("{label} was unmounted but could not be powered off:\n{detail}\n\n"
                       "You can still unplug it.").format(label=label, detail=detail))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    @pyqtSlot(str)
    def showSmart(self, device):
        threading.Thread(target=self._smart, args=(device,),
                         daemon=True).start()

    def _smart(self, device):
        base = get_base_disk_device(device)

        def run(cmd, timeout):
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout)

        try:
            result = run(["smartctl", "-a", base], 30)
        except FileNotFoundError:
            self.messageRequested.emit(
                tr("SMART unavailable"),
                tr("The smartctl command was not found.\n\n"
                   "Install the smartmontools package to enable this feature."))
            return
        except subprocess.TimeoutExpired:
            self.messageRequested.emit(
                tr("SMART unavailable"), tr("Reading SMART timed out."))
            return

        combined = (result.stdout + result.stderr).lower()
        if "permission denied" in combined or "must be root" in combined:
            try:
                result = run(["pkexec", "smartctl", "-a", base], 90)
            except FileNotFoundError:
                self.messageRequested.emit(
                    tr("Insufficient rights"),
                    tr("Reading SMART requires root rights, and pkexec "
                       "(graphical elevation) was not found on this system."))
                return
            except subprocess.TimeoutExpired:
                self.messageRequested.emit(
                    tr("SMART unavailable"), tr("Reading SMART timed out."))
                return

        output = (result.stdout.strip() or result.stderr.strip()
                  or tr("No information available."))
        self.smartReady.emit("SMART - " + base, output)

    # santeDetails (2.0): background reading, result through healthReady
    @pyqtSlot(str)
    def healthDetails(self, device):
        # immediate reading: a test started earlier is seen as "in progress" as soon as the window opens
        threading.Thread(target=lambda: self.healthReady.emit(device, health_details(device, True)),
                         daemon=True).start()

    @pyqtSlot(str)
    def healthRefresh(self, device):
        threading.Thread(target=lambda: self.healthReady.emit(device, health_details(device, True)),
                         daemon=True).start()

    # disk self-test: "short", "extended" or "abort" (password asked by polkit)
    @pyqtSlot(str, str)
    def selftest(self, device, action):
        if action in ("short", "extended", "abort"):
            threading.Thread(target=self._selftest, args=(device, action), daemon=True).start()

    def _selftest(self, device, action):
        drive = _drive_of(device)
        if not drive:
            return
        iface = _UDISKS + (".NVMe.Controller" if _udisks_tout(drive, _UDISKS + ".NVMe.Controller")
                           else ".Drive.Ata")
        try:
            if action == "abort":
                _udisks_call(drive, iface, "SmartSelftestAbort", "a{sv}", 0, interactif=True, delai=130)
            else:
                _udisks_call(drive, iface, "SmartSelftestStart", "sa{sv}", action, 0, interactif=True, delai=130)
        except Exception as e:
            if "already" in str(e):
                # the disk (or udisks2) does not accept a new test yet, even if it announces
                # the previous one as finished: wait about a minute
                self.selftestDone.emit(device, False)
                self.messageRequested.emit(tr("Disk self-test"),
                                           tr("The disk is still finishing the previous test. Try again in a minute."))
                return
            self.selftestDone.emit(device, False)
            if not annulation_polkit(e):   # password window closed or cancelled: nothing to report
                self.messageRequested.emit(tr("Disk self-test"),
                                           tr("The test could not be started.") + "\n\n" + str(e)[:300])
            return
        self.selftestDone.emit(device, action != "abort")

    # analyseSurface (2.0): one analysis at a time, state sent through surfaceUpdated
    @pyqtSlot(str)
    def surfaceStart(self, device):
        base = get_base_disk_device(device)
        if not re.match(r"^/dev/[A-Za-z0-9]+$", base) or (self._surface and self._surface.get("running")):
            return
        self._surface = {"device": device, "running": True, "zones": [None] * SURFACE_ZONES,
                         "taille": 0, "secteur": 512, "debut": time.time(), "fin": False, "proc": None}
        self._surfaceEmit()
        threading.Thread(target=self._surfaceRun, args=(base,), daemon=True).start()

    @pyqtSlot(str)
    def surfaceStop(self, device):
        s = self._surface
        if s and s.get("proc") and s["device"] == device:
            try:
                s["proc"].stdin.write("stop\n")
                s["proc"].stdin.flush()
            except (OSError, ValueError):
                pass

    @pyqtSlot(str)
    def surfaceRefresh(self, device):
        if self._surface and self._surface["device"] == device:
            self._surfaceEmit()

    def _surfaceEmit(self):
        s = self._surface
        faites = [z for z in s["zones"] if z]
        octets = sum(z[0] for z in faites)
        secondes = sum(z[1] for z in faites)
        etats = surface_classer(s["zones"])
        reste = -1
        if s["running"] and faites and octets:
            reste = int((time.time() - s["debut"]) / len(faites) * (SURFACE_ZONES - len(faites)))
        self.surfaceUpdated.emit({
            "device": s["device"], "running": s["running"], "finished": s["fin"],
            "states": etats, "done": len(faites), "total": SURFACE_ZONES,
            "speed": float(octets / secondes / 1e6) if secondes > 0 else -1.0,
            "slow": etats.count(2), "errors": sum(z[2] for z in faites), "errorZones": etats.count(3), "left": reste,
            # sectors (disk unit): total, read, and size of a zone; 0 until the disk is opened
            "sectors": float(s["taille"] // s["secteur"]), "sectorsRead": float(octets // s["secteur"]),
            "zoneSectors": float(s["taille"] // s["secteur"] // SURFACE_ZONES),
            "slowSectors": float(sum(z[0] for z, e in zip(s["zones"], etats) if e == 2) // s["secteur"])})

    def _surfaceRun(self, base):
        s = self._surface
        python = shutil.which("python3") or "/usr/bin/python3"
        try:
            p = subprocess.Popen(["pkexec", python, "-I", "-c", SURFACE_LECTEUR, base, str(SURFACE_ZONES)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 text=True)
        except OSError:
            p = None
        s["proc"] = p
        dernier = 0.0
        if p:
            for ligne in p.stdout:
                m = ligne.split()
                if len(m) == 3 and m[0] == "taille":
                    try:
                        s["taille"], s["secteur"] = int(m[1]), int(m[2])
                    except ValueError:
                        pass
                elif len(m) == 5 and m[0] == "zone":
                    try:
                        s["zones"][int(m[1])] = (int(m[2]), float(m[3]), int(m[4]))
                    except (ValueError, IndexError):
                        continue
                    if time.time() - dernier > 0.3:   # at most ~3 updates per second
                        dernier = time.time()
                        self._surfaceEmit()
                elif m == ["fin"]:
                    s["fin"] = all(s["zones"])
            p.wait()
            if p.returncode in (126, 127) and not any(s["zones"]):   # password refused or cancelled
                s["fin"] = False
        else:
            self.messageRequested.emit(tr("Surface scan"), tr("The pkexec command was not found."))
        s["running"] = False
        s["proc"] = None
        self._surfaceEmit()

    # santeUsb (2.0): reading through the USB enclosure, on demand (password)
    @pyqtProperty(bool, constant=True)
    def hasSmartctl(self):
        return shutil.which("smartctl") is not None

    @pyqtSlot(str)
    def healthUsb(self, device):
        def travail():
            s, err = health_usb(device)
            self.healthReady.emit(device, s)
            if err in ("bloque", "erreur"):
                self.messageRequested.emit(tr("Disk health"),
                                           tr("The USB enclosure of this disk does not pass on its health information."))
        threading.Thread(target=travail, daemon=True).start()

    # testsUsb (2.0): one USB test at a time; state sent through usbTestUpdated(device, {etat, reste, code})
    @pyqtSlot(str, str)
    def selftestUsb(self, device, genre):
        base = get_base_disk_device(device)
        sm = shutil.which("smartctl")
        if genre not in ("short", "extended") or not sm or not re.match(r"^/dev/[A-Za-z0-9]+$", base) \
                or getattr(self, "_usbTest", None):
            return
        threading.Thread(target=self._selftestUsb, args=(device, base, genre, sm), daemon=True).start()

    @pyqtSlot(str)
    def selftestUsbStop(self, device):
        p = getattr(self, "_usbTest", None)
        if p:
            try:
                p.stdin.write("stop\n")
                p.stdin.flush()
            except (OSError, ValueError):
                pass

    def _selftestUsb(self, device, base, genre, sm):
        python = shutil.which("python3") or "/usr/bin/python3"
        try:
            p = subprocess.Popen(["pkexec", python, "-I", "-c", USB_TESTEUR, base,
                                  "long" if genre == "extended" else "short", sm],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            self.usbTestUpdated.emit(device, {"etat": "annule"})
            return
        self._usbTest = p
        fini = False
        for ligne in p.stdout:
            m = ligne.split()
            if len(m) == 2 and m[0] == "prog":
                self.usbTestUpdated.emit(device, {"etat": "test", "reste": int(m[1])})
            elif len(m) == 2 and m[0] == "fin":
                code = int(m[1])   # 0 succeeded, 1 stopped, 2 interrupted, other: failed
                self.usbTestUpdated.emit(device, {"etat": "fin", "code": code})
                fini = True
            elif m == ["refus"]:
                self.messageRequested.emit(tr("Disk self-test"), tr("The test could not be started."))
        p.wait()
        self._usbTest = None
        if not fini:   # password cancelled, or test refused
            self.usbTestUpdated.emit(device, {"etat": "annule"})

    # formatage (2.0): all volumes except the system ones; udisks2 does the work (polkit password)
    # info for the Format window, read in the background then sent through formatInfoReady
    @pyqtSlot(str)
    def formatInfo(self, device):
        def travail():
            try:
                nom = self._driveName(device)
            except Exception:
                nom = ""
            self.formatInfoReady.emit(device, nom, self._formatSupport())
        threading.Thread(target=travail, daemon=True).start()

    def _formatSupport(self):
        """Formats proposés et disponibles sur ce système : {type: outil manquant ou ""}."""
        res = {}
        for fs in ("vfat", "exfat", "ntfs", "ext4"):
            try:
                v = _udisks_call("/org/freedesktop/UDisks2/Manager", _UDISKS + ".Manager", "CanFormat", "s", fs)
                ok, manque = v[0] if len(v) == 1 else v   # udisks2 answer: a structure (bs)
                res[fs] = "" if ok else (manque or fs)
            except Exception:
                res[fs] = "?"
        return res

    def _driveName(self, device):
        drive = _drive_of(device)
        if not drive:
            return ""
        d = _udisks_tout(drive, _UDISKS + ".Drive")
        marque, modele = disk_identity(d.get("Vendor"), d.get("Model"))
        return (marque + " " + modele).strip()

    @pyqtSlot(str, str, str, bool)
    def formatVolume(self, device, fs, label, rapide):
        if fs in ("vfat", "exfat", "ntfs", "ext4") and re.match(r"^/dev/[A-Za-z0-9]+$", device) \
                and not volume_systeme(device):
            carte = next((d for d in self._disks if d["device"] == device), None)
            if carte:
                if not hasattr(self, "_formatEnCours"):
                    self._formatEnCours = {}
                self._formatEnCours[device] = carte
            threading.Thread(target=self._format, args=(device, fs, label, rapide), daemon=True).start()

    # full formatting: "Cancel" stops the udisks2 task (filling with zeros) of the volume
    @pyqtSlot(str)
    def formatCancel(self, device):
        bloc = "/org/freedesktop/UDisks2/block_devices/" + os.path.basename(device)

        def travail():
            try:
                r = subprocess.run(["busctl", "--system", "tree", "--list", _UDISKS],
                                   capture_output=True, text=True, timeout=3)
                for objet in r.stdout.split():
                    if "/jobs/" in objet:
                        j = _udisks_tout(objet, _UDISKS + ".Job")
                        if bloc in (j.get("Objects") or []) and j.get("Cancelable"):
                            _udisks_call(objet, _UDISKS + ".Job", "Cancel", "a{sv}", 0, interactif=True)
            except Exception:
                pass
        threading.Thread(target=travail, daemon=True).start()

    def _finFormat(self, device, ok):
        getattr(self, "_formatEnCours", {}).pop(device, None)
        self.formatDone.emit(device, ok)

    def _suiviFormat(self, device, bloc, fin):
        """Formatage complet : avancement lu sur la tâche udisks2 du volume, envoyé par formatProgress."""
        while not fin.wait(2):
            try:
                r = subprocess.run(["busctl", "--system", "tree", "--list", _UDISKS],
                                   capture_output=True, text=True, timeout=3)
                for objet in r.stdout.split():
                    if "/jobs/" in objet:
                        j = _udisks_tout(objet, _UDISKS + ".Job")
                        if bloc in (j.get("Objects") or []) and j.get("ProgressValid"):
                            self.formatProgress.emit(device, float(j.get("Progress", 0)))
            except Exception:
                pass

    def _format(self, device, fs, label, rapide=True):
        titre = tr("Format")
        if device_mountpoints(device):
            r = subprocess.run(["udisksctl", "unmount", "-b", device], capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                self._finFormat(device, False)
                self.messageRequested.emit(titre, tr("The volume could not be unmounted. Close the files and "
                                                     "windows that use it, then try again.") + "\n\n" + r.stderr.strip()[:300])
                return
        bloc = "/org/freedesktop/UDisks2/block_devices/" + os.path.basename(device)
        options = ["label", "s", label, "update-partition-type", "b", "true"]
        if not rapide:   # full formatting: the disk is first filled with zeros
            options += ["erase", "s", "zero"]
        fin = threading.Event()
        if not rapide:
            threading.Thread(target=self._suiviFormat, args=(device, bloc, fin), daemon=True).start()
        try:
            _udisks_call(bloc, _UDISKS + ".Block", "Format", "sa{sv}", fs, len(options) // 3, *options,
                         interactif=True, delai=900 if rapide else 172800)
        except Exception as e:
            fin.set()
            self._finFormat(device, False)
            if not annulation_polkit(e) and "ancel" not in str(e):   # password cancelled, or cancellation requested
                self.messageRequested.emit(titre, tr("Formatting failed.") + "\n\n" + str(e)[:300])
            subprocess.run(["udisksctl", "mount", "-b", device], capture_output=True, timeout=30)
            return
        fin.set()
        subprocess.run(["udisksctl", "mount", "-b", device], capture_output=True, timeout=30)
        self._finFormat(device, True)   # the list of disks updates itself (3 s)

    # Filelight (space analysis): button shown only if it is installed
    # simpleClic (2.0, request #2): follows the Plasma setting "Clicking files or folders:
    # open them" both ways (Plasma -> This PC live; the Options box -> all of Plasma)
    def _relireSimpleClic(self):
        v = lire_simple_clic()
        if v != self._simpleClic:
            self._simpleClic = v
            self.optionsChanged.emit()

    @pyqtProperty(bool, notify=optionsChanged)
    def singleClick(self):
        return self._simpleClic

    @pyqtSlot(bool)
    def setSingleClick(self, oui):
        try:
            subprocess.run(["kwriteconfig6", "--file", "kdeglobals", "--group", "KDE", "--key", "SingleClick",
                            "true" if oui else "false"], timeout=10, check=True)
            # like System Settings: Plasma and the KDE applications read the setting again
            # (KGlobalSettings, settings change = 3, mouse category = 0)
            subprocess.run(["dbus-send", "--session", "--type=signal", "/KGlobalSettings",
                            "org.kde.KGlobalSettings.notifyChange", "int32:3", "int32:0"], timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        self._relireSimpleClic()

    # seuilAlerte (2.0, request #1): gauge turns red below this percentage of free space (10 to 20 %)
    @pyqtProperty(int, notify=optionsChanged)
    def alertPercent(self):
        return self._seuil

    @pyqtSlot(int)
    def setAlertPercent(self, n):
        n = max(10, min(20, int(n)))
        if n == self._seuil:
            return
        self._seuil = n
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = OPTIONS_CONFIG + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"seuil": n, "compact": self._compact}, f)
            os.replace(tmp, OPTIONS_CONFIG)
        except OSError:
            pass
        self.optionsChanged.emit()

    # barreCompacte: toolbar with icons only (same commands), set in the Options
    @pyqtProperty(bool, notify=optionsChanged)
    def compactBar(self):
        return self._compact

    @pyqtSlot(bool)
    def setCompactBar(self, v):
        v = bool(v)
        if v == self._compact:
            return
        self._compact = v
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = OPTIONS_CONFIG + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"seuil": self._seuil, "compact": v}, f)
            os.replace(tmp, OPTIONS_CONFIG)
        except OSError:
            pass
        self.optionsChanged.emit()

    @pyqtProperty(bool, constant=True)
    def hasFilelight(self):
        return shutil.which("filelight") is not None

    @pyqtSlot(str)
    def analyseSpace(self, path):
        if path.startswith("/"):
            try:
                subprocess.Popen(["filelight", path])
            except OSError:
                pass

    @pyqtSlot(str, str, str)
    def renameDevice(self, device, fstype, label):
        threading.Thread(target=self._rename, args=(device, fstype, label),
                         daemon=True).start()

    def _rename(self, device, fstype, label):
        titre = tr("Cannot rename")
        label = label.strip()
        fs = fstype.lower()
        limites = {"ntfs": 128, "vfat": 11, "exfat": 15,
                   "ext2": 16, "ext3": 16, "ext4": 16,
                   "btrfs": 255, "xfs": 12}
        a_chaud = {"ext2", "ext3", "ext4", "btrfs", "xfs"}

        if fs not in limites:
            self.messageRequested.emit(
                titre, tr("Renaming is not supported for this format ({fstype}).").format(fstype=fstype))
            return
        if not label or "/" in label or any(ord(c) < 32 for c in label):
            self.messageRequested.emit(
                titre, tr("The name is empty or contains a forbidden character."))
            return
        if len(label.encode("utf-8")) > limites[fs]:
            self.messageRequested.emit(
                titre, tr("The name is too long for this format (maximum {max} characters).").format(max=limites[fs]))
            return

        if fs not in a_chaud:
            try:
                r = subprocess.run(["udisksctl", "unmount", "-b", device],
                                   capture_output=True, text=True, timeout=15)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                self.messageRequested.emit(titre, tr("Unmounting the disk failed."))
                return
            if r.returncode != 0:
                detail = r.stderr.strip() or tr("unknown error")
                self.messageRequested.emit(
                    titre,
                    tr("Could not unmount the disk:\n{detail}\n\n"
                       "Close the windows and applications that use this drive, then try again.").format(detail=detail))
                return

        obj = "/org/freedesktop/UDisks2/block_devices/" + os.path.basename(device)
        valeur = "'" + label.replace("\\", "\\\\").replace("'", "\\'") + "'"
        ok = False
        detail = ""
        try:
            r = subprocess.run(
                ["gdbus", "call", "--system", "--dest", "org.freedesktop.UDisks2",
                 "--object-path", obj,
                 "--method", "org.freedesktop.UDisks2.Filesystem.SetLabel",
                 valeur, "{}"],
                capture_output=True, text=True, timeout=30)
            ok = r.returncode == 0
            detail = r.stderr.strip() or tr("unknown error")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            detail = tr("gdbus not found or timed out.")

        if fs not in a_chaud:
            try:
                r = subprocess.run(["udisksctl", "mount", "-b", device],
                                   capture_output=True, text=True, timeout=15)
                if r.returncode != 0:
                    self.messageRequested.emit(
                        tr("Remount failed"),
                        tr("The disk could not be remounted:\n{detail}\n\n"
                           "Unplug it and plug it back in.").format(
                               detail=r.stderr.strip() or tr("unknown error")))
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        if not ok:
            self.messageRequested.emit(titre, detail)

    @pyqtSlot(str)
    def openFolder(self, path):
        if path.startswith("optical:"):
            threading.Thread(target=self._openOptical, args=(path[8:],), daemon=True).start()
            return
        try:
            subprocess.Popen(["dolphin", "--new-window", path])
        except FileNotFoundError:
            try:
                subprocess.Popen(["xdg-open", path])
            except Exception:
                print(f"Impossible d'ouvrir {path}")


QML = """
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Controls.Basic as Basic
import QtQuick.Layouts
import QtQuick.Effects
import org.kde.kirigami as Kirigami

QQC2.ApplicationWindow {
    id: win
    visible: true
    title: trad("This PC")
    // minRuban: the window does not shrink below the width of the ribbon (it depends on the language)
    minimumWidth: Math.max(740, (backend.compactBar ? compactRow.implicitWidth : ribbonRow.implicitWidth) + 20)
    width: Math.max(minimumWidth, winInit.w > 0 ? winInit.w : 850)   // tailleInitiale
    // minHauteur: with the ribbon, the first row of disks must be fully visible
    minimumHeight: 520
    height: Math.max(520, winInit.h > 0 ? winInit.h : 584)
    // fenetreMemo: the size is saved on closing (visibility 4 = maximized window)
    // fenetreAutonome: closing This PC leaves the properties open (a self-test can last);
    // the program stops when the last window is closed
    onClosing: backend.saveWindow(win.width, win.height, win.visibility === 4)
    Component.onCompleted: {
        if (winInit.max)
            win.showMaximized()
    }
    color: Kirigami.Theme.backgroundColor

    // rubriquesMemo (This PC 1.1): collapsed state of each section and sections shown, kept from one opening to the next
    property bool foldersCollapsed: sectInit.folders.collapsed
    property bool disksCollapsed: sectInit.disks.collapsed
    property bool netCollapsed: sectInit.net.collapsed
    property bool printersCollapsed: sectInit.printers.collapsed
    property bool foldersShown: sectInit.folders.shown
    property bool disksShown: sectInit.disks.shown
    property bool netShown: sectInit.net.shown
    property bool printersShown: sectInit.printers.shown
    function saveSections() {
        backend.saveSections(JSON.stringify({
            folders: { collapsed: foldersCollapsed, shown: foldersShown },
            disks: { collapsed: disksCollapsed, shown: disksShown },
            net: { collapsed: netCollapsed, shown: netShown },
            printers: { collapsed: printersCollapsed, shown: printersShown } }))
    }
    onFoldersCollapsedChanged: saveSections()
    onDisksCollapsedChanged: saveSections()
    onNetCollapsedChanged: saveSections()
    onPrintersCollapsedChanged: saveSections()
    onFoldersShownChanged: saveSections()
    onDisksShownChanged: saveSections()
    onNetShownChanged: saveSections()
    onPrintersShownChanged: saveSections()
    // Name of a section without its counter "({n})" (check boxes of the "Show" group)
    function sectName(s) {
        return s.replace("({n})", "").replace("（{n}）", "").trim()
    }
    // seuilAlerte: beyond this occupation (fraction of the disk), the gauges turn red
    readonly property real alertFrac: 1 - backend.alertPercent / 100   // seuilAlerte (2.0): adjustable, 20 % by default
    // couleursJauge: blue of normal gauges and red of gauges in alert (fixed)
    readonly property color gaugeBlue: "#006CE0"   // 2.1: Fenestra blue, readable in light and dark themes, the same everywhere in the theme
    readonly property color gaugeRed: "#d92b2b"
    // Disk or network location selected by a click (mount point), empty if none
    property string selectedKey: ""

    property var propsDisk: ({})

    // grilleCartes: cards of fixed width, as many columns as the room allows
    // (usable area = scroll width - 10 - margins 2 x 15; 8 of spacing between columns)
    readonly property real cardWidth: 340
    readonly property int cardCols: Math.max(1, Math.floor((scroll.availableWidth - 32) / (cardWidth + 8)))


    // fenestraLangues: translated text (table sent by the program), English text if absent
    function trad(s) {
        var v = trTable[s]
        return v !== undefined ? v : s
    }

    // Disk or network location currently selected (null if nothing)
    readonly property var selEntry: {
        var k = selectedKey
        if (k === "")
            return null
        for (var i = 0; i < backend.disks.length; i++)
            if (backend.disks[i].mountpoint === k)
                return backend.disks[i]
        for (var j = 0; j < backend.networks.length; j++)
            if (backend.networks[j].mountpoint === k)
                return backend.networks[j]
        return null
    }

    function go(n) {
        return (n / 1073741824).toFixed(1).replace(".", trDecimal)
    }
    // size: GB with one decimal under 1 TB, then TB with three significant digits
    function taille(n) {
        var g = n / 1073741824
        if (Math.round(g * 10) / 10 < 1024)
            return g.toFixed(1).replace(".", trDecimal) + " " + trad("GB")
        var t = g / 1024
        return t.toFixed(t < 10 ? 2 : (t < 100 ? 1 : 0)).replace(".", trDecimal) + " " + trad("TB")
    }
    function showPrinter(d) {
        printerWin.ouvrir(d)
    }
    function showProps(d) {
        propsDisk = d
        propsWin.sante = ({})
        propsWin.phase = ""
        propsWin.surface = ({})
        propsWin.serieVisible = false
        propsWin.testResultat = ""
        propsTabs.currentIndex = 0
        backend.surfaceRefresh(d.device)
        renameField.initial = d.rawLabel || d.label || ""
        renameField.text = renameField.initial
        // the window opens once the health has been read (3 s at most): its size is then final
        if (propsWin.santePossible) {
            propsWin.attenteOuverture = true
            ouvertureMax.restart()
            backend.healthDetails(d.device)
        } else {
            Qt.callLater(propsWin.ouvrir)
        }
    }
    Timer {
        id: ouvertureMax
        interval: 3000
        onTriggered: if (propsWin.attenteOuverture) {
            propsWin.attenteOuverture = false
            propsWin.ouvrir()
        }
    }
    // number with the thousands separator of the system language
    function nombre(n) {
        // language separator set by hand: Qt leaves it out on some 4-digit numbers ("1000")
        return String(Math.round(n)).replace(/\\B(?=(\\d{3})+(?!\\d))/g, Qt.locale().groupSeparator)
    }

    // "label … value" line with a rule below (mock-up)
    // 2.1: slider drawn in Fenestra blue (the Plasma style ignores the palette)
    component BleuCurseur: Basic.Slider {
        id: sl
        background: Item {
            x: sl.leftPadding
            y: sl.topPadding
            width: sl.availableWidth
            height: sl.availableHeight
            Rectangle {   // groove
                y: (parent.height - height) / 2
                width: parent.width
                height: 6
                radius: 3
                color: Qt.alpha(Kirigami.Theme.textColor, 0.2)
                Rectangle {   // filled part
                    width: sl.position * parent.width
                    x: sl.mirrored ? parent.width - width : 0
                    height: parent.height
                    radius: 3
                    color: win.gaugeBlue
                }
            }
            Repeater {   // tick marks, one per step
                model: Math.round((sl.to - sl.from) / sl.stepSize) + 1
                Rectangle {
                    x: sl.handle.width / 2 + index * (parent.width - sl.handle.width) / (Math.round((sl.to - sl.from) / sl.stepSize))
                    y: parent.height / 2 + 8
                    width: 1
                    height: 6
                    color: win.gaugeBlue
                }
            }
        }
        handle: Rectangle {
            x: sl.leftPadding + sl.visualPosition * (sl.availableWidth - width)
            y: sl.topPadding + (sl.availableHeight - height) / 2
            implicitWidth: 20
            implicitHeight: 20
            width: 20
            height: 20
            radius: 10
            color: Kirigami.Theme.backgroundColor
            border.width: sl.pressed ? 2 : 1
            border.color: sl.pressed || sl.hovered ? win.gaugeBlue : Qt.alpha(Kirigami.Theme.textColor, 0.45)
        }
    }

    component Ligne: ColumnLayout {
        id: ligne
        property string libelle: ""
        property string valeur: ""
        Layout.fillWidth: true
        spacing: 6
        RowLayout {
            Layout.fillWidth: true
            QQC2.Label { text: ligne.libelle; opacity: 0.75 }
            Item { Layout.fillWidth: true }
            QQC2.Label {
                text: ligne.valeur
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideMiddle
                Layout.maximumWidth: 330
            }
        }
        Kirigami.Separator { Layout.fillWidth: true; opacity: 0.5 }
    }

    component LigneZone: RowLayout {
        id: lz
        property color puce: "transparent"
        property string libelle: ""
        property string valeur: ""
        Layout.fillWidth: true
        spacing: 6
        Rectangle { width: 10; height: 10; color: lz.puce }
        QQC2.Label { text: lz.libelle; opacity: 0.8 }
        Item { Layout.fillWidth: true }
        QQC2.Label { text: lz.valeur; horizontalAlignment: Text.AlignRight }
    }

    // menuDisque (2.0): menu outside the card; the list of disks is read again every 3 s and
    // recreated the cards, which closed the menu before the click (two clicks were needed)
    property var menuDisk: ({})
    QQC2.Menu {
        id: diskMenu
        QQC2.MenuItem {
            text: win.trad("Open with Dolphin")
            icon.name: "folder-open"
            onTriggered: backend.openFolder(win.menuDisk.mountpoint)
        }
        QQC2.MenuItem {
            text: win.trad("Safely remove")
            icon.name: "media-eject"
            visible: win.menuDisk.isUsb === true
            height: visible ? implicitHeight : 0
            onTriggered: backend.ejectDevice(win.menuDisk.device, win.menuDisk.label)
        }
        QQC2.MenuItem {
            text: win.trad("Eject")
            icon.name: "media-eject"
            visible: win.menuDisk.isOptical === true
            height: visible ? implicitHeight : 0
            onTriggered: backend.ejectOptical(win.menuDisk.device)
        }
        QQC2.MenuItem {   // formatage (2.0): never for a system volume or an optical drive
            text: win.trad("Format…")
            icon.name: "edit-clear-all"
            visible: win.menuDisk.isOptical !== true && ["/", "/boot", "/boot/efi", "/efi", "/home", "/usr", "/var"]
                     .indexOf(win.menuDisk.mountpoint || "") < 0
            height: visible ? implicitHeight : 0
            onTriggered: formatWin.ouvrir(win.menuDisk)
        }
        QQC2.MenuItem {
            text: win.trad("Properties")
            icon.name: "document-properties"
            onTriggered: win.showProps(win.menuDisk)
        }
    }

    // ongletsClassiques (2.0): selected tab in the colour of the page, bordered and open towards the bottom,
    // accent line on top; unselected tabs lower, slightly tinted
    component Onglet: QQC2.TabButton {
        id: og
        readonly property color filet: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.25)
        implicitWidth: libelleOnglet.implicitWidth + 32
        implicitHeight: libelleOnglet.implicitHeight + 16
        width: implicitWidth
        contentItem: QQC2.Label {   // text placed by us (the Plasma style no longer displayed it with this background)
            id: libelleOnglet
            text: og.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            color: Kirigami.Theme.textColor
        }
        background: Item {
            clip: true   // rounded corners at the top only: the rectangle overflows at the bottom and is clipped
            Rectangle {
                y: og.checked ? 0 : 3
                width: parent.width
                height: parent.height - y + radius + 2   // no border at the bottom: it is the line under the tabs
                radius: 6
                color: og.checked ? Kirigami.Theme.backgroundColor
                       : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b,
                                 og.hovered ? 0.06 : 0.03)
                border.width: 1
                border.color: og.filet
                Rectangle {   // accent line on top of the selected tab, between the rounded corners
                    visible: og.checked
                    anchors { left: parent.left; right: parent.right; top: parent.top; leftMargin: 4; rightMargin: 4 }
                    height: 2
                    color: win.gaugeBlue   // 2.1: Fenestra blue, like the rest of the theme
                }
            }
        }
    }

    // proprietesFenetre (This PC 2.0): real window (movable), "General" and "Health" tabs
    Window {
        id: propsWin
        // fenetreAutonome: without a parent, it stays open if This PC is minimized or closed
        transientParent: null
        flags: Qt.Window
        modality: Qt.NonModal
        title: win.trad("Properties of {name}").replace("{name}", win.propsDisk.label || "")
        width: 540
        minimumWidth: 540
        maximumWidth: 540
        // tailleFigee: height fixed when opening (tallest tab), no maximizing and no resizing
        property bool attenteOuverture: false
        function ouvrir() {
            // disks: fixed size (Surface tab, the tallest, fully visible)
            var h = santePossible ? 708 : Math.ceil(propsCol.implicitHeight + 32 + 50)
            maximumHeight = 100000
            minimumHeight = 0
            height = h
            minimumHeight = h
            maximumHeight = h
            show()
            raise()
            requestActivate()
        }
        color: Kirigami.Theme.backgroundColor

        property var sante: ({})
        property var surface: ({})   // analyseSurface (2.0)
        property bool serieVisible: false
        readonly property bool santePossible: !win.propsDisk.isNetwork && win.propsDisk.isOptical !== true
        readonly property bool vieConnue: sante.lifeUsed !== undefined && sante.lifeUsed >= 0
        // deroulementTest (2.0): a single state variable, changed only in onHealthReady
        //   ""        no test being followed
        //   "attente" test requested, not yet seen "in progress"
        //   "test"    the disk is testing (bar: 0 to 70 % quick, 0 to 95 % full, at the disk's pace)
        //   "cloture" the disk has finished, the system (udisks2) keeps the test ~30 s: the bar goes on, slowing down
        //   "arrivee" everything is released: the bar finishes its run up to 100 % (1.5 s)
        //   "fini"    result in colour, buttons active again
        property string phase: ""
        property string testType: "short"
        property string testResultat: ""
        property real finDebut: 0
        property real maintenant: 0
        readonly property bool enTest: phase === "attente" || phase === "test"
        readonly property bool enCoursAffiche: phase === "attente" || phase === "test" || phase === "cloture" || phase === "arrivee"
        readonly property bool libre: (phase === "" || phase === "fini") && sante.jobBusy !== true
        function lancerTest(type) {
            testType = type
            testResultat = ""
            phase = "attente"
            if (sante.viaUsb === true)   // disk in a USB enclosure: test through smartctl
                backend.selftestUsb(win.propsDisk.device, type)
            else
                backend.selftest(win.propsDisk.device, type)
        }
        Timer { id: arriveeFin; interval: 1500; onTriggered: if (propsWin.phase === "arrivee") propsWin.phase = "fini" }
        readonly property real progression: {
            var part = sante.viaUsb === true ? 100 : (testType === "short" ? 70 : 95)   // USB: no closing phase
            if (phase === "arrivee" || phase === "fini")
                return 100
            if (phase === "cloture")
                return part + (99 - part) * (1 - Math.exp(-Math.max(0, maintenant - finDebut) / 15000))
            if (phase === "test" && sante.selftest === "inprogress" && sante.selftestLeft >= 0)
                return (100 - sante.selftestLeft) * part / 100
            return 0
        }
        Timer {
            interval: 1000
            repeat: true
            running: propsWin.visible && propsWin.phase === "cloture"
            triggeredOnStart: true
            onTriggered: propsWin.maintenant = Date.now()
        }
        readonly property string typeTexte: {
            var s = sante
            var t = s.kind === "nvme" ? "SSD NVMe" : (s.kind === "ssd" ? "SSD SATA"
                    : (s.kind === "hdd" ? win.trad("Hard disk drive") : ""))
            return t !== "" && s.usb ? t + " · USB" : t
        }
        readonly property color etatCouleur: sante.state === "fail" ? "#c42b1c"
                                             : (sante.state === "warn" ? "#d35400"
                                             : (sante.state === "good" ? "#2e9d4f" : Kirigami.Theme.disabledTextColor))

        Shortcut {
            sequence: "Escape"
            onActivated: propsWin.close()
        }

        Timer {   // during a self-test: read again every 2 s
            interval: 2000
            repeat: true
            running: propsWin.visible && propsWin.sante.viaUsb !== true   // USB: followed by testsUsb
                     && (propsWin.enTest || propsWin.phase === "cloture"
                         || (propsWin.phase === "" && propsWin.sante.jobBusy === true))
            onTriggered: backend.healthRefresh(win.propsDisk.device)
        }
        Timer {   // the test never started: we stop waiting
            id: attenteMax
            interval: 60000
            onTriggered: if (propsWin.phase === "attente") propsWin.phase = ""
        }

        Connections {
            target: backend
            function onHealthReady(device, s) {
                if (device !== win.propsDisk.device)
                    return
                var p = propsWin.phase
                if (s.selftest === "inprogress") {
                    if (p === "" || p === "attente" || p === "fini")
                        propsWin.phase = "test"
                } else if (p === "test" && s.selftest !== "") {
                    propsWin.testResultat = s.selftest   // the disk has finished: we keep its result
                    propsWin.finDebut = Date.now()
                    propsWin.maintenant = propsWin.finDebut
                    propsWin.phase = "cloture"
                }
                propsWin.sante = s
                if (propsWin.phase === "cloture" && s.jobBusy === false) {   // released (safe reading)
                    propsWin.phase = "arrivee"
                    arriveeFin.restart()
                }
                if (propsWin.attenteOuverture) {
                    propsWin.attenteOuverture = false
                    Qt.callLater(propsWin.ouvrir)
                }
            }
            function onUsbTestUpdated(device, m) {   // testsUsb: same sequence as the internal disks
                if (device !== win.propsDisk.device || propsWin.sante.viaUsb !== true)
                    return
                var s = Object.assign({}, propsWin.sante)
                if (m.etat === "test") {
                    s.selftest = "inprogress"
                    s.selftestLeft = m.reste
                    propsWin.sante = s
                    if (propsWin.phase !== "test")
                        propsWin.phase = "test"
                } else if (m.etat === "fin") {
                    s.selftest = m.code === 0 ? "success" : (m.code === 1 ? "aborted" : (m.code === 2 ? "interrupted" : "fatal"))
                    s.selftestLeft = -1
                    propsWin.sante = s
                    propsWin.testResultat = s.selftest
                    propsWin.phase = "arrivee"
                    arriveeFin.restart()
                } else if (propsWin.phase === "attente" || propsWin.phase === "test") {
                    propsWin.phase = ""
                }
            }
            function onSurfaceUpdated(s) {
                propsWin.surface = s
            }
            function onSelftestDone(device, ok) {
                if (device !== win.propsDisk.device)
                    return
                if (!ok && propsWin.phase === "attente")
                    propsWin.phase = ""
                if (ok)
                    attenteMax.restart()
                backend.healthDetails(device)
            }
        }

        ColumnLayout {
            id: propsCol
            x: 16
            y: 16
            width: parent.width - 32
            spacing: 0

            QQC2.TabBar {
                id: propsTabs
                Layout.fillWidth: true
                visible: propsWin.santePossible
                spacing: 2
                background: Item {}
                Onglet { text: win.trad("General") }
                Onglet { text: win.trad("Health") }
                Onglet { text: win.trad("Surface") }
            }
            // traitOnglets: line under the tabs, interrupted under the selected tab (tab "open" onto its content)
            Item {
                id: traitOnglets
                Layout.fillWidth: true
                implicitHeight: 1
                visible: propsTabs.visible
                readonly property var actif: propsTabs.itemAt(propsTabs.currentIndex)
                readonly property real debut: actif ? propsTabs.leftPadding + actif.x : 0
                readonly property real fin: actif ? debut + actif.width : 0
                readonly property color couleur: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                                         Kirigami.Theme.textColor.b, 0.25)
                // up to the edges of the window (beyond the 16 px margin of the content)
                Rectangle { x: -propsCol.x; width: traitOnglets.debut + propsCol.x; height: 1; color: traitOnglets.couleur }
                Rectangle {
                    x: traitOnglets.fin
                    width: Math.max(0, traitOnglets.width - traitOnglets.fin + propsCol.x)
                    height: 1
                    color: traitOnglets.couleur
                }
            }

            StackLayout {
                Layout.fillWidth: true
                // height of the tallest tab: the window does not change size when going from one tab to another
                Layout.preferredHeight: propsWin.santePossible
                                        ? Math.max(pageGeneral.implicitHeight, pageSante.implicitHeight, pageSurface.implicitHeight)
                                        : pageGeneral.implicitHeight
                Layout.topMargin: propsWin.santePossible ? 14 : 0
                currentIndex: propsWin.santePossible ? propsTabs.currentIndex : 0

                // ── General ──────────────────────────────────────────────
                ColumnLayout {
                    id: pageGeneral
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 14
                        Kirigami.Icon {
                            source: win.propsDisk.icon || "drive-harddisk"
                            Layout.preferredWidth: 48
                            Layout.preferredHeight: 48
                        }
                        // rename: USB sticks and external disks only
                        QQC2.TextField {
                            id: renameField
                            visible: win.propsDisk.isUsb === true && win.propsDisk.mountpoint !== "/"
                                     && !win.propsDisk.isNetwork
                            Layout.fillWidth: true
                            placeholderText: win.trad("Volume name")
                            property string initial: ""
                        }
                        QQC2.Button {
                            visible: renameField.visible
                            text: win.trad("Rename")
                            enabled: renameField.text.trim() !== "" && renameField.text !== renameField.initial
                            onClicked: {
                                backend.renameDevice(win.propsDisk.device, win.propsDisk.fstype,
                                                     renameField.text)
                                propsWin.close()
                            }
                        }
                        QQC2.Label {
                            visible: !renameField.visible
                            text: win.propsDisk.label || ""
                            font.pointSize: Kirigami.Theme.defaultFont.pointSize * 1.15
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }

                    Kirigami.Separator { Layout.fillWidth: true; opacity: 0.5 }

                    QQC2.Label { text: win.trad("Information"); font.bold: true; Layout.topMargin: 4 }
                    Ligne {
                        libelle: win.trad("Type")
                        valeur: (win.propsDisk.isNetwork ? win.trad("Network location")
                                : (win.propsDisk.isOptical === true ? win.trad("CD/DVD Drive")
                                : (win.propsDisk.isUsb ? win.trad("Removable disk") : win.trad("Local Disk"))))
                                + (propsWin.typeTexte !== "" ? " · " + propsWin.typeTexte : "")
                    }
                    // identiteDisque (2.0): lines hidden when the disk does not give the information
                    Ligne { libelle: win.trad("Brand"); valeur: propsWin.sante.brand || ""; visible: valeur !== "" }
                    Ligne { libelle: win.trad("Model"); valeur: propsWin.sante.model || ""; visible: valeur !== "" }
                    Ligne { libelle: win.trad("Firmware"); valeur: propsWin.sante.firmware || ""; visible: valeur !== "" }
                    ColumnLayout {   // serial number: hidden by default (shared screenshots)
                        Layout.fillWidth: true
                        spacing: 6
                        visible: (propsWin.sante.serial || "") !== ""
                        RowLayout {
                            Layout.fillWidth: true
                            QQC2.Label { text: win.trad("Serial number"); opacity: 0.75 }
                            Item { Layout.fillWidth: true }
                            QQC2.Label {
                                readonly property string sn: propsWin.sante.serial || ""
                                text: propsWin.serieVisible || sn.length < 7 ? sn
                                      : sn.slice(0, 4) + "•".repeat(sn.length - 6) + sn.slice(-2)
                            }
                            QQC2.ToolButton {
                                icon.name: propsWin.serieVisible ? "view-hidden" : "view-visible"
                                display: QQC2.AbstractButton.IconOnly
                                implicitWidth: 26
                                implicitHeight: 22
                                QQC2.ToolTip.visible: hovered
                                QQC2.ToolTip.text: propsWin.serieVisible ? win.trad("Hide") : win.trad("Show")
                                onClicked: propsWin.serieVisible = !propsWin.serieVisible
                            }
                        }
                        Kirigami.Separator { Layout.fillWidth: true; opacity: 0.5 }
                    }

                    Ligne { libelle: win.trad("File system"); valeur: win.propsDisk.fstype || "" }
                    Ligne { libelle: win.trad("Mount point"); valeur: win.propsDisk.mountpoint || "" }
                    Ligne {
                        libelle: win.trad("Device")
                        valeur: win.propsDisk.device || ""
                        visible: !win.propsDisk.isNetwork
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 16   // disk space: 10 px lower than originally
                        spacing: 22

                        Canvas {   // occupation ring, percentage in the centre
                            id: pie
                            Layout.preferredWidth: 110
                            Layout.preferredHeight: 110
                            property real frac: win.propsDisk.total > 0
                                                ? win.propsDisk.used / win.propsDisk.total : 0
                            property color usedColor: frac >= win.alertFrac ? win.gaugeRed : win.gaugeBlue
                            property color freeColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                                              Kirigami.Theme.textColor.g,
                                                              Kirigami.Theme.textColor.b, 0.12)
                            onFracChanged: requestPaint()
                            onUsedColorChanged: requestPaint()
                            onFreeColorChanged: requestPaint()
                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.reset()
                                var cx = width / 2, cy = height / 2, r = 46
                                ctx.lineWidth = 18
                                ctx.strokeStyle = freeColor
                                ctx.beginPath()
                                ctx.arc(cx, cy, r, 0, 2 * Math.PI)
                                ctx.stroke()
                                if (frac > 0) {
                                    ctx.strokeStyle = usedColor
                                    ctx.beginPath()
                                    ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * frac)
                                    ctx.stroke()
                                }
                            }
                            QQC2.Label {
                                anchors.centerIn: parent
                                text: Math.round(pie.frac * 100) + " %"
                                font.bold: true
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            RowLayout {
                                Layout.fillWidth: true
                                Rectangle { width: 10; height: 10; radius: 2; color: pie.usedColor }
                                QQC2.Label { text: win.trad("Used space") }
                                Item { Layout.fillWidth: true }
                                QQC2.Label { text: win.taille(win.propsDisk.used || 0) }
                            }
                            Kirigami.Separator { Layout.fillWidth: true; opacity: 0.5 }
                            RowLayout {
                                Layout.fillWidth: true
                                Rectangle { width: 10; height: 10; radius: 2; color: pie.freeColor }
                                QQC2.Label { text: win.trad("Free space") }
                                Item { Layout.fillWidth: true }
                                QQC2.Label { text: win.taille(win.propsDisk.free || 0) }
                            }
                            Kirigami.Separator { Layout.fillWidth: true; opacity: 0.5 }
                            RowLayout {
                                Layout.fillWidth: true
                                Item { width: 10; height: 10 }
                                QQC2.Label { text: win.trad("Capacity"); font.bold: true }
                                Item { Layout.fillWidth: true }
                                QQC2.Label { text: win.taille(win.propsDisk.total || 0); font.bold: true }
                            }
                        }
                    }

                    RowLayout {
                        Layout.topMargin: 21   // "Open in Dolphin": 25 px lower than originally (10 from the ring + 15 here)
                        spacing: 8
                        QQC2.Button {
                            text: win.trad("Open in Dolphin")
                            visible: win.propsDisk.isOptical !== true && (win.propsDisk.mountpoint || "") !== ""
                            onClicked: backend.openFolder(win.propsDisk.mountpoint)
                        }
                        QQC2.Button {   // Filelight, only if it is installed
                            text: win.trad("Analyze space…")
                            visible: backend.hasFilelight && !win.propsDisk.isNetwork
                                     && win.propsDisk.isOptical !== true
                            onClicked: backend.analyseSpace(win.propsDisk.mountpoint)
                        }
                    }
                }

                // ── Health ────────────────────────────────────────────────
                ColumnLayout {
                    id: pageSante
                    spacing: 10

                    Rectangle {   // state banner
                        Layout.fillWidth: true
                        implicitHeight: bandeau.implicitHeight + 24
                        radius: 6
                        color: Qt.rgba(propsWin.etatCouleur.r, propsWin.etatCouleur.g, propsWin.etatCouleur.b, 0.12)
                        border.width: 1
                        border.color: Qt.rgba(propsWin.etatCouleur.r, propsWin.etatCouleur.g, propsWin.etatCouleur.b, 0.35)
                        RowLayout {
                            id: bandeau
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 12
                            Rectangle {
                                Layout.preferredWidth: 30
                                Layout.preferredHeight: 30
                                radius: 15
                                color: propsWin.etatCouleur
                                QQC2.Label {
                                    anchors.centerIn: parent
                                    color: "white"
                                    font.bold: true
                                    text: propsWin.sante.state === "good" ? "✓"
                                          : (propsWin.sante.state === "fail" ? "✕"
                                          : (propsWin.sante.state === "warn" ? "!" : "?"))
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                QQC2.Label {
                                    font.bold: true
                                    font.pointSize: Kirigami.Theme.defaultFont.pointSize * 1.15
                                    text: propsWin.sante.state === undefined ? win.trad("Reading…")
                                          : (propsWin.sante.state === "good" ? win.trad("Good condition")
                                          : (propsWin.sante.state === "warn" ? win.trad("Signs of wear")
                                          : (propsWin.sante.state === "fail" ? win.trad("Disk failing – back up your files")
                                          : win.trad("Health information not available"))))
                                }
                                QQC2.Label {
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                    opacity: 0.75
                                    visible: text !== ""
                                    text: {
                                        var s = propsWin.sante
                                        if (s.state === undefined)
                                            return ""
                                        if (!s.available)
                                            return win.trad("This disk does not report its health (usual for USB disks, memory cards and virtual disks).")
                                        var r = s.reasons || [], t = []
                                        if (r.indexOf("fail:critical") >= 0) t.push(win.trad("The disk reports a serious fault."))
                                        if (r.indexOf("fail:spare") >= 0) t.push(win.trad("The spare area is used up."))
                                        if (r.indexOf("warn:sectors") >= 0) t.push(win.trad("Data errors have been detected."))
                                        if (r.indexOf("warn:life") >= 0) t.push(win.trad("The disk is near the end of its expected life."))
                                        if (r.indexOf("warn:temp") >= 0) t.push(win.trad("The disk is too hot."))
                                        if (r.indexOf("warn:test") >= 0) t.push(win.trad("The last self-test failed."))
                                        if (t.length === 0) {
                                            t.push(win.trad("No sign of abnormal wear."))
                                            if (s.selftest === "success")
                                                t.push(win.trad("Last self-test: passed."))
                                        }
                                        return t.join(" ")
                                    }
                                }
                                QQC2.Button {   // santeUsb: disk in a USB enclosure
                                    visible: propsWin.sante.state !== undefined && propsWin.sante.available !== true
                                             && propsWin.sante.usb === true && backend.hasSmartctl
                                    text: win.trad("Read through the USB enclosure")
                                    Layout.topMargin: 4
                                    onClicked: backend.healthUsb(win.propsDisk.device)
                                }
                            }
                        }
                    }

                    // Wear
                    QQC2.Label {
                        text: win.trad("Wear"); font.bold: true; Layout.topMargin: 4
                        visible: propsWin.vieConnue
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 5
                        visible: propsWin.vieConnue
                        RowLayout {
                            Layout.fillWidth: true
                            QQC2.Label { text: win.trad("Lifetime remaining"); opacity: 0.75 }
                            Item { Layout.fillWidth: true }
                            QQC2.Label { text: Math.max(0, 100 - (propsWin.sante.lifeUsed || 0)) + " %" }
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 9   // lifetime: one and a half times the previous height
                            radius: 3
                            color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.12)
                            Rectangle {
                                height: parent.height
                                radius: 3
                                anchors.right: parent.right   // the blue stays on the right and greys out from the left
                                visible: Math.max(0, 100 - (propsWin.sante.lifeUsed || 0)) > 0
                                width: Math.max(height, parent.width * Math.max(0, 100 - (propsWin.sante.lifeUsed || 0)) / 100)
                                color: (propsWin.sante.lifeUsed || 0) >= 90 ? "#c0392b" : ((propsWin.sante.lifeUsed || 0) >= 80 ? "#d35400" : win.gaugeBlue)
                            }
                        }
                        QQC2.Label {
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                            opacity: 0.65
                            font.pointSize: Kirigami.Theme.smallFont.pointSize
                            text: win.trad("Manufacturer's estimate. At 0 %, the disk has reached its expected lifespan.")
                        }
                    }

                    // Details
                    QQC2.Label {
                        text: win.trad("Details"); font.bold: true; Layout.topMargin: 4
                        visible: propsWin.sante.available === true
                    }
                    Repeater {
                        model: {
                            var s = propsWin.sante, l = []
                            if (s.available !== true)
                                return l
                            if (s.spare >= 0)
                                l.push([win.trad("Available spare"), s.spare + " %"])
                            if (s.errors >= 0)
                                l.push([s.kind === "nvme" ? win.trad("Data errors") : win.trad("Bad sectors"),
                                        s.errors === 0 && s.kind === "nvme" ? win.trad("None") : win.nombre(s.errors)])
                            if (s.hours >= 0)
                                l.push([win.trad("Power-on time"),
                                        win.trad("{h} h (about {d} days)").replace("{h}", win.nombre(s.hours))
                                                                          .replace("{d}", win.nombre(Math.round(s.hours / 24)))])
                            if (s.cycles >= 0)
                                l.push([win.trad("Power cycles"), win.nombre(s.cycles)])
                            if (s.unsafe >= 0)
                                l.push([win.trad("Unexpected shutdowns"), win.nombre(s.unsafe)])
                            if (s.written >= 0)
                                l.push([win.trad("Data written"), win.taille(s.written)])
                            if (s.read >= 0)
                                l.push([win.trad("Data read"), win.taille(s.read)])
                            if (s.temp > -999)
                                l.push([win.trad("Temperature"), s.tempWarn > -999
                                        ? win.trad("{t} °C (warning at {w} °C)").replace("{t}", s.temp).replace("{w}", s.tempWarn)
                                        : s.temp + " °C"])
                            if (propsWin.typeTexte !== "")
                                l.push([win.trad("Type"), propsWin.typeTexte])
                            return l
                        }
                        delegate: Ligne { libelle: modelData[0]; valeur: modelData[1] }
                    }

                    // Disk self-test (administrator password asked by the system)
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 6
                        spacing: 8
                        // buttons always visible, greyed out during a test (avoids a second launch)
                        visible: propsWin.sante.available === true
                        // during a test: the button of the running test turns blue (not clickable), the others are greyed out
                        QQC2.Button {
                            readonly property bool actif: propsWin.enCoursAffiche && propsWin.testType === "short"
                            text: win.trad("Run a quick test")
                            highlighted: true
                            enabled: propsWin.libre || actif
                            onClicked: if (propsWin.libre) propsWin.lancerTest("short")
                            Rectangle {   // test in progress: solid button in the accent colour, like the active filter of Fenestra Search
                                anchors.fill: parent
                                visible: parent.actif
                                radius: 4
                                color: win.gaugeBlue   // royal blue, like the active capsule of Fenestra Search
                                QQC2.Label {
                                    anchors.centerIn: parent
                                    text: parent.parent.text
                                    color: Kirigami.Theme.textColor.hslLightness > 0.5 ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.backgroundColor
                                }
                                MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }   // not clickable
                            }
                        }
                        QQC2.Button {
                            readonly property bool actif: propsWin.enCoursAffiche && propsWin.testType === "extended"
                            text: win.trad("Full test")
                            enabled: propsWin.libre || actif
                            onClicked: if (propsWin.libre) propsWin.lancerTest("extended")
                            Rectangle {   // test in progress: solid button in the accent colour, like the active filter of Fenestra Search
                                anchors.fill: parent
                                visible: parent.actif
                                radius: 4
                                color: win.gaugeBlue   // royal blue, like the active capsule of Fenestra Search
                                QQC2.Label {
                                    anchors.centerIn: parent
                                    text: parent.parent.text
                                    color: Kirigami.Theme.textColor.hslLightness > 0.5 ? Kirigami.Theme.highlightedTextColor : Kirigami.Theme.backgroundColor
                                }
                                MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }   // not clickable
                            }
                        }
                        QQC2.Button {
                            text: win.trad("Stop")   // short: the four buttons fit on one line
                            visible: propsWin.enCoursAffiche
                            onClicked: if (propsWin.phase === "test") {   // during the closing phase, the test is already finished: nothing to stop
                                if (propsWin.sante.viaUsb === true)
                                    backend.selftestUsbStop(win.propsDisk.device)
                                else
                                    backend.selftest(win.propsDisk.device, "abort")
                            }
                        }
                        Item { Layout.fillWidth: true }
                        QQC2.Button {
                            text: win.trad("Detailed report…")
                            flat: true
                            enabled: propsWin.libre
                            onClicked: backend.showSmart(win.propsDisk.device)
                        }
                    }
                    // progressionTest: bar that moves smoothly, then result shown at 100 %
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 6
                        spacing: 6
                        visible: propsWin.sante.available === true && propsWin.phase !== ""
                        // "finished" only when the system has released everything (buttons active again)
                        readonly property bool fini: propsWin.phase === "fini"
                        readonly property bool reussi: propsWin.testResultat === "success"
                        readonly property color teinte: !fini ? win.gaugeBlue
                                                        : (reussi ? "#2e9d4f" : (propsWin.testResultat === "aborted"
                                                           ? Kirigami.Theme.disabledTextColor : "#c42b1c"))
                        id: suiviTest
                        RowLayout {
                            Layout.fillWidth: true
                            QQC2.Label {
                                font.bold: suiviTest.fini
                                color: suiviTest.fini ? suiviTest.teinte : Kirigami.Theme.textColor
                                text: {
                                    var nom = propsWin.testType === "extended" ? win.trad("Full test") : win.trad("Quick test")
                                    if (!suiviTest.fini)
                                        return win.trad("{test} in progress…").replace("{test}", nom)
                                    if (suiviTest.reussi)
                                        return win.trad("{test} complete: passed.").replace("{test}", nom)
                                    if (propsWin.testResultat === "aborted" || propsWin.testResultat === "interrupted")
                                        return win.trad("{test} stopped.").replace("{test}", nom)
                                    return win.trad("{test} complete: failed. Back up your files.").replace("{test}", nom)
                                }
                            }
                            Item { Layout.fillWidth: true }
                            // figure read from the bar itself: it moves with it, without getting ahead of it
                            QQC2.Label { text: (pisteTest.width > 0 ? Math.round(remplissage.width / pisteTest.width * 100) : 0) + " %" }
                        }
                        Rectangle {
                            id: pisteTest
                            Layout.fillWidth: true
                            implicitHeight: 16   // test bar (quick and full): twice as tall, slightly rounded corners
                            radius: 3
                            color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                           Kirigami.Theme.textColor.b, 0.12)
                            Rectangle {
                                id: remplissage
                                height: parent.height
                                radius: 3
                                color: suiviTest.teinte
                                width: parent.width * propsWin.progression / 100
                                Behavior on width {   // glides between two readings; at the end, 100 % in 1.5 s
                                    NumberAnimation { duration: propsWin.phase === "arrivee" ? 1500 : 1800; easing.type: Easing.Linear }
                                }
                            }
                        }
                    }
                    QQC2.Label {
                        visible: propsWin.sante.available === true && propsWin.phase === ""
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        opacity: 0.65
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                        text: win.trad("Quick test: from a few seconds to 2 minutes. Full test: from several minutes to several hours. You can keep using the computer.")
                    }
                }

                // ── Surface (surface analysis) ─────────────────────────
                ColumnLayout {
                    id: pageSurface
                    spacing: 10
                    readonly property var sf: propsWin.surface.device === win.propsDisk.device ? propsWin.surface : ({})
                    readonly property bool enCours: sf.running === true
                    function duree(t) {
                        var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60
                        return h > 0 ? win.trad("{h} h {m} min").replace("{h}", h).replace("{m}", m)
                                     : win.trad("{m} min {s} s").replace("{m}", m).replace("{s}", s)
                    }

                    QQC2.Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        opacity: 0.75
                        text: win.trad("Reads every sector of the disk without writing anything. Each square is a group of sectors.")
                    }

                    Grid {
                        Layout.alignment: Qt.AlignHCenter
                        // 10 px squares with square corners, 2 px gap: same area as the original grid
                        columns: 40
                        spacing: 2
                        Repeater {
                            model: 1000
                            Rectangle {
                                width: 10
                                height: 10
                                readonly property int etat: pageSurface.sf.states ? (pageSurface.sf.states[index] || 0) : 0
                                color: etat === 1 ? "#2e9d4f" : (etat === 2 ? "#e0a800" : (etat === 3 ? "#c42b1c"
                                       : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                                 Kirigami.Theme.textColor.b, 0.12)))
                            }
                        }
                    }

                    // figures box: fixed columns, values right-aligned (they no longer move)
                    Rectangle {
                        id: cadre
                        Layout.fillWidth: true
                        implicitHeight: encadre.implicitHeight + 20
                        color: "transparent"
                        radius: 4
                        border.width: 1
                        border.color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                              Kirigami.Theme.textColor.b, 0.2)
                        readonly property var sf: pageSurface.sf
                        readonly property int faites: sf.done || 0
                        readonly property color gris: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                                              Kirigami.Theme.textColor.b, 0.12)
                        ColumnLayout {
                            id: encadre
                            anchors.fill: parent
                            anchors.margins: 10
                            spacing: 6
                            // sectors: disk unit (total, read, unreadable); zones: speed measured per group
                            LigneZone {
                                libelle: win.trad("Number of sectors")
                                valeur: (cadre.sf.sectors || 0) > 0 ? win.nombre(cadre.sf.sectors) : "—"
                            }
                            LigneZone {
                                libelle: win.trad("One square")
                                valeur: (cadre.sf.zoneSectors || 0) > 0
                                        ? win.trad("{n} sectors").replace("{n}", win.nombre(cadre.sf.zoneSectors)) : "—"
                            }
                            Kirigami.Separator { Layout.fillWidth: true; opacity: 0.5 }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 20
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    spacing: 4
                                    LigneZone { puce: cadre.gris; libelle: win.trad("Not read")
                                                valeur: (cadre.sf.sectors || 0) > 0
                                                        ? win.nombre(cadre.sf.sectors - cadre.sf.sectorsRead) : "—" }
                                    LigneZone { libelle: win.trad("Scanned")
                                                valeur: win.nombre(cadre.sf.sectorsRead || 0) + " (" + Math.floor(cadre.faites / 10) + " %)" }
                                    LigneZone { libelle: win.trad("Speed")
                                                valeur: (cadre.sf.speed || -1) >= 0
                                                        ? win.trad("{v} MB/s").replace("{v}", win.nombre(cadre.sf.speed)) : "—" }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1
                                    spacing: 4
                                    LigneZone { puce: "#2e9d4f"; libelle: win.trad("Normal")
                                                valeur: win.nombre(Math.max(0, (cadre.sf.sectorsRead || 0)
                                                        - (cadre.sf.slowSectors || 0) - (cadre.sf.errors || 0))) }
                                    LigneZone { puce: "#e0a800"; libelle: win.trad("Slow")
                                                valeur: win.nombre(cadre.sf.slowSectors || 0) }
                                    LigneZone { puce: "#c42b1c"; libelle: win.trad("Damaged")
                                                valeur: win.nombre(cadre.sf.errors || 0) }
                                }
                            }
                            Kirigami.Separator { Layout.fillWidth: true; opacity: 0.5 }
                            LigneZone {
                                libelle: win.trad("Time left")
                                valeur: cadre.sf.running === true && cadre.sf.left >= 0
                                        ? pageSurface.duree(cadre.sf.left) : "—"
                            }
                        }
                    }

                    Rectangle {   // conclusion, as a banner like the Health tab
                        id: conclusion
                        readonly property var sf: pageSurface.sf
                        // "" (nothing to say), "stop", "fail", "warn", "good"
                        readonly property string etat: sf.running !== false || (sf.done || 0) === 0 ? ""
                                                       : (!sf.finished ? "stop" : (sf.errors > 0 ? "fail"
                                                       : (sf.slow > 0 ? "warn" : "good")))
                        readonly property color teinte: etat === "fail" ? "#c42b1c" : (etat === "warn" ? "#d35400"
                                                        : (etat === "good" ? "#2e9d4f" : Kirigami.Theme.disabledTextColor))
                        visible: etat !== ""
                        Layout.fillWidth: true
                        implicitHeight: concl.implicitHeight + 20
                        radius: 6
                        color: Qt.rgba(teinte.r, teinte.g, teinte.b, 0.12)
                        border.width: 1
                        border.color: Qt.rgba(teinte.r, teinte.g, teinte.b, 0.35)
                        ColumnLayout {
                            id: concl
                            anchors.fill: parent
                            anchors.margins: 10
                            spacing: 2
                            QQC2.Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                font.bold: true
                                color: conclusion.etat === "stop" ? Kirigami.Theme.textColor : conclusion.teinte
                                text: conclusion.etat === "stop" ? win.trad("Scan stopped.")
                                      : (conclusion.etat === "fail"
                                         ? win.trad("Scan complete: {n} damaged sectors.").replace("{n}", win.nombre(conclusion.sf.errors))
                                      : (conclusion.etat === "warn" ? win.trad("Scan complete: some sectors are slow.")
                                      : win.trad("Scan complete: no problem found.")))
                            }
                            QQC2.Label {
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                                opacity: 0.8
                                visible: text !== ""
                                text: conclusion.etat === "fail"
                                      ? win.trad("Back up your files now and plan to replace this disk. A file stored on a damaged sector can be lost.")
                                      : (conclusion.etat === "warn"
                                         ? win.trad("Keep an eye on this disk: run the scan again later to compare.") : "")
                            }
                        }
                    }

                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter
                        spacing: 8
                        QQC2.Button {
                            text: win.trad("Start the scan")
                            highlighted: true
                            visible: !pageSurface.enCours
                            onClicked: backend.surfaceStart(win.propsDisk.device)
                        }
                        QQC2.Button {
                            text: win.trad("Stop")
                            visible: pageSurface.enCours
                            onClicked: backend.surfaceStop(win.propsDisk.device)
                        }
                    }
                    QQC2.Label {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        horizontalAlignment: Text.AlignHCenter
                        opacity: 0.65
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                        visible: !conclusion.visible
                        text: win.trad("Your administrator password is asked to read the disk. You can keep working; the disk will be a little slower.")
                    }
                }
            }
        }

        // boutonFixe: "Close" anchored at the bottom of the window, same place on the three tabs
        QQC2.Button {
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 16
            text: win.trad("Close")
            onClicked: propsWin.close()
        }

        QQC2.Dialog {
            id: smartDialog
            modal: true
            anchors.centerIn: QQC2.Overlay.overlay
            width: 500
            height: 420
            standardButtons: QQC2.Dialog.Close

            contentItem: QQC2.ScrollView {
                QQC2.TextArea {
                    id: smartText
                    readOnly: true
                    wrapMode: TextEdit.NoWrap
                    font.family: "monospace"
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                }
            }
        }
    }

    Connections {
        target: backend
        function onSmartReady(titre, texte) {
            smartDialog.title = titre
            smartText.text = texte
            smartDialog.open()
        }
        function onNetworksChanged() {
            if (!propsWin.visible)
                return
            for (var k = 0; k < backend.networks.length; k++) {
                if (backend.networks[k].mountpoint === win.propsDisk.mountpoint) {
                    win.propsDisk = backend.networks[k]
                    break
                }
            }
        }
        function onDisksChanged() {
            if (!propsWin.visible)
                return
            for (var i = 0; i < backend.disks.length; i++) {
                if (backend.disks[i].mountpoint === win.propsDisk.mountpoint) {
                    win.propsDisk = backend.disks[i]
                    break
                }
            }
        }
    }

    QQC2.Dialog {
        id: msgDialog
        modal: true
        anchors.centerIn: QQC2.Overlay.overlay
        standardButtons: QQC2.Dialog.Ok
        width: 400
        property string discoverId: ""   // discoverApp (2.0): if set, the button opens Discover on this program
        ColumnLayout {
            width: parent.width
            spacing: 12
            QQC2.Label {
                id: msgLabel
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }
            QQC2.Button {
                visible: msgDialog.discoverId !== ""
                text: win.trad("Search in Discover")
                icon.name: "plasma-discover"
                onClicked: {
                    backend.openDiscover(msgDialog.discoverId)
                    msgDialog.close()
                }
            }
        }
    }
    Connections {
        target: backend
        function onSharesFound(liste) {
            addNetDialog.busy = false
            addNetDialog.msg = ""
            addNetDialog.found = liste
            netFound.currentIndex = -1
        }
        function onSharesError(texte) {
            addNetDialog.busy = false
            addNetDialog.found = []
            addNetDialog.msg = texte
        }
        function onMessageRequested(titre, texte) {
            msgDialog.discoverId = ""
            msgDialog.title = titre
            msgLabel.text = texte
            msgDialog.open()
        }
        function onMessageAppRequested(titre, texte, appId) {
            msgDialog.discoverId = appId
            msgDialog.title = titre
            msgLabel.text = texte
            msgDialog.open()
        }
    }

    QQC2.Dialog {
        id: addNetDialog
        property var found: []
        property string msg: ""
        property bool busy: false
        modal: true
        anchors.centerIn: QQC2.Overlay.overlay
        title: win.trad("Add a network location")
        width: 480
        standardButtons: QQC2.Dialog.Ok | QQC2.Dialog.Cancel
        onAboutToShow: {
            netName.text = ""
            netHost.text = ""
            netShare.text = ""
            netUser.text = ""
            netPass.text = ""
            netRemember.checked = false
            addNetDialog.found = []
            addNetDialog.msg = ""
            addNetDialog.busy = false
        }
        onClosed: netPass.text = ""
        onAccepted: backend.addNetwork(netName.text, netHost.text, netShare.text, netUser.text,
                                       netPass.text, netRemember.checked)

        contentItem: GridLayout {
            columns: 2
            columnSpacing: 12
            rowSpacing: 8

            QQC2.Label { text: win.trad("Server address:"); opacity: 0.75 }
            QQC2.TextField {
                id: netHost
                Layout.fillWidth: true
                placeholderText: win.trad("192.168.1.10, nas-name or smb://...")
                onTextChanged: {
                    // Pasted address: smb://user@server/share
                    var m = /^ *smb:[/][/](?:([^@/]+)@)?([^/ ]+)(?:[/]([^/ ]+))?/i.exec(text)
                    if (m) {
                        var u = m[1] ? m[1] : ""
                        try { u = decodeURIComponent(u) } catch (e) {}
                        var s = m[3] ? m[3] : ""
                        try { s = decodeURIComponent(s) } catch (e) {}
                        netUser.text = u !== "" ? u : netUser.text
                        if (s !== "")
                            netShare.text = s
                        text = m[2]
                    }
                }
            }
            QQC2.Label { text: win.trad("Username (required):"); opacity: 0.75 }
            QQC2.TextField { id: netUser; Layout.fillWidth: true }
            QQC2.Label { text: win.trad("Password:"); opacity: 0.75 }
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                QQC2.TextField {
                    id: netPass
                    Layout.fillWidth: true
                    echoMode: TextInput.Password
                    placeholderText: win.trad("only for “List”, never saved")
                }
                QQC2.Button {
                    text: addNetDialog.busy ? win.trad("Searching…") : win.trad("List")
                    enabled: !addNetDialog.busy && netHost.text.trim() !== "" && netUser.text.trim() !== ""
                    onClicked: {
                        addNetDialog.msg = ""
                        addNetDialog.found = []
                        addNetDialog.busy = true
                        backend.listShares(netHost.text, netUser.text, netPass.text)
                    }
                }
            }
            // Always shown (greyed out until "List" has found shares) so the window never changes size
            QQC2.Label { text: win.trad("Shares found:"); opacity: addNetDialog.found.length > 0 ? 0.75 : 0.4 }
            QQC2.ComboBox {
                id: netFound
                Layout.fillWidth: true
                enabled: addNetDialog.found.length > 0
                model: addNetDialog.found
                currentIndex: -1
                displayText: currentIndex < 0 ? win.trad("Choose a share…") : currentText
                onActivated: netShare.text = currentText
            }
            QQC2.Label { text: win.trad("Share name (required):"); opacity: 0.75 }
            QQC2.TextField { id: netShare; Layout.fillWidth: true; placeholderText: win.trad("filled in from the list, or type it") }
            QQC2.Label { text: win.trad("Display name (optional):"); opacity: 0.75 }
            QQC2.TextField { id: netName; Layout.fillWidth: true; placeholderText: win.trad("My NAS") }
            QQC2.CheckBox {
                id: netRemember
                Layout.columnSpan: 2
                Layout.fillWidth: true
                enabled: netPass.text !== ""
                text: win.trad("Remember the password (KWallet) to show the free space")
            }
            QQC2.Label {
                Layout.columnSpan: 2
                Layout.fillWidth: true
                visible: addNetDialog.msg !== ""
                wrapMode: Text.Wrap
                color: Kirigami.Theme.negativeTextColor
                text: addNetDialog.msg
            }
            QQC2.Label {
                Layout.columnSpan: 2
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                opacity: 0.75
                text: win.trad("The final password is requested by Dolphin the first time you open it; you can ask it to remember it.")
            }
        }
    }

    // imprimantesInfo (2.0): real independent window, presented like Properties
    Window {
        id: printerWin
        transientParent: null
        flags: Qt.Window
        modality: Qt.NonModal
        title: win.trad("Properties of {name}").replace("{name}", info.label || "")
        width: 480
        minimumWidth: 480
        maximumWidth: 480
        height: prnCol.implicitHeight + 32 + 50
        color: Kirigami.Theme.backgroundColor
        property var info: ({})

        function ouvrir(d) {
            info = d
            maximumHeight = 100000
            minimumHeight = 0
            show()
            raise()
            requestActivate()
            Qt.callLater(function() {   // size fixed once the window is laid out
                printerWin.minimumHeight = printerWin.height
                printerWin.maximumHeight = printerWin.height
            })
        }

        Shortcut {
            sequence: "Escape"
            onActivated: printerWin.close()
        }

        ColumnLayout {
            id: prnCol
            x: 16
            y: 16
            width: parent.width - 32
            spacing: 10

            RowLayout {   // header: printer icon and name
                Layout.fillWidth: true
                spacing: 14
                Kirigami.Icon {
                    source: printerWin.info.icon || "printer"
                    Layout.preferredWidth: 48
                    Layout.preferredHeight: 48
                }
                QQC2.Label {
                    text: printerWin.info.label || ""
                    font.pointSize: Kirigami.Theme.defaultFont.pointSize * 1.15
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }

            Kirigami.Separator { Layout.fillWidth: true; opacity: 0.5 }

            QQC2.Label { text: win.trad("Information"); font.bold: true; Layout.topMargin: 4 }
            Ligne { libelle: win.trad("Type"); valeur: win.trad("Printer") }
            Ligne { libelle: win.trad("Status"); valeur: printerWin.info.statusText || "" }
            Ligne { visible: !!printerWin.info.addr; libelle: win.trad("IP address"); valeur: printerWin.info.addr || "" }
            Ligne { libelle: win.trad("Device"); valeur: printerWin.info.device || "" }

            QQC2.Button {
                visible: printerWin.info.absent === true
                text: win.trad("Remove from list")
                onClicked: {
                    backend.forgetPrinter(printerWin.info.name)
                    printerWin.close()
                }
            }

            QQC2.Label {
                text: win.trad("Ink levels")
                font.bold: true
                Layout.topMargin: 4
                visible: (printerWin.info.inks || []).length > 0
            }
            Repeater {   // one line per cartridge: name, rounded bar in its colour, percentage
                model: printerWin.info.inks || []
                delegate: RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    QQC2.Label {
                        text: modelData.name
                        opacity: 0.75
                        elide: Text.ElideRight
                        Layout.preferredWidth: 130
                    }
                    Rectangle {   // bar with straight ends, taller (like the cards and the disk analysis)
                        Layout.fillWidth: true
                        implicitHeight: 14
                        radius: 0
                        color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                       Kirigami.Theme.textColor.b, 0.12)
                        Rectangle {
                            height: parent.height
                            radius: 0
                            width: modelData.level > 0 ? Math.max(2, parent.width * modelData.level / 100) : 0
                            color: modelData.color
                            border.width: 1   // discreet outline: yellow stays visible on a light background
                            border.color: Qt.rgba(0, 0, 0, 0.15)
                        }
                    }
                    QQC2.Label {
                        text: modelData.level + " %"
                        color: modelData.low ? "#c42b1c" : Kirigami.Theme.textColor
                        font.bold: modelData.low
                        horizontalAlignment: Text.AlignRight
                        Layout.preferredWidth: 44
                    }
                }
            }
        }

        QQC2.Button {   // "Close" anchored at the bottom of the window, like Properties
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 16
            text: win.trad("Close")
            onClicked: printerWin.close()
        }
    }

    // formatage (2.0): real independent window (like Properties), Windows 11 style
    Window {
        id: formatWin
        transientParent: null
        flags: Qt.Window
        modality: Qt.NonModal
        title: win.trad("Format {name}").replace("{name}", disque.label || "")
        width: 480
        minimumWidth: 480
        maximumWidth: 480
        height: formatCol.implicitHeight + 32
        color: Kirigami.Theme.backgroundColor
        property var disque: ({})
        property string nomDisque: ""
        property var formats: ({})
        property bool infosLues: false   // available formats known: "Format" button enabled
        property bool enCours: false
        property string resultat: ""     // "", "ok", "erreur"
        property real avance: -1         // 0 to 1 during a full format; -1 = unknown (quick format)
        property bool complet: false     // format in progress without "quick": can be cancelled
        property bool annule: false
        // [udisks2 type, displayed name, explanation, maximum length of the name]
        readonly property var choix: [
            ["exfat", "exFAT", win.trad("Windows, Mac and Linux, files of any size. Best for USB sticks and external disks."), 15],
            ["vfat", "FAT32", win.trad("Works everywhere (TV, car radio, old devices), but no file larger than 4 GB."), 11],
            ["ntfs", "NTFS", win.trad("For a disk used mainly with Windows."), 32],
            ["ext4", "ext4", win.trad("For a disk used only with Linux."), 16]]
        readonly property var choisi: choix[formatCombo.currentIndex]
        readonly property string manque: formats[choisi[0]] || ""

        function ouvrir(d) {
            disque = d
            enCours = false
            resultat = ""
            avance = -1
            nomDisque = ""
            formats = ({})
            infosLues = false
            rapide.checked = true
            backend.formatInfo(d.device || "")
            formatCombo.currentIndex = d.isUsb === true ? 0 : 3
            nomVolume.text = (d.rawLabel || "").slice(0, choix[formatCombo.currentIndex][3])
            maximumHeight = 100000
            minimumHeight = 0
            show()
            raise()
            requestActivate()
            Qt.callLater(function() {   // size fixed once the window is laid out
                formatWin.minimumHeight = formatWin.height
                formatWin.maximumHeight = formatWin.height
            })
        }
        onClosing: function(close) { if (enCours) close.accepted = false }   // no closing in the middle of a format

        Connections {
            target: backend
            function onFormatInfoReady(device, nom, formats) {
                if (device !== formatWin.disque.device)
                    return
                formatWin.nomDisque = nom || ""
                formatWin.formats = formats || ({})
                formatWin.infosLues = true
            }
            function onFormatProgress(device, valeur) {
                if (device === formatWin.disque.device)
                    formatWin.avance = valeur
            }
            function onFormatDone(device, ok) {
                if (device !== formatWin.disque.device)
                    return
                formatWin.enCours = false
                formatWin.resultat = ok ? "ok" : (formatWin.annule ? "annule" : "erreur")
            }
        }

        ColumnLayout {
            id: formatCol
            x: 16
            y: 16
            width: parent.width - 32
            spacing: 10

            RowLayout {   // header: which disk is being formatted
                Layout.fillWidth: true
                spacing: 14
                Kirigami.Icon {
                    source: formatWin.disque.icon || "drive-harddisk"
                    Layout.preferredWidth: 56
                    Layout.preferredHeight: 56
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    QQC2.Label {
                        text: formatWin.disque.label || ""
                        font.pixelSize: 22
                        font.bold: true
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    QQC2.Label {
                        text: [formatWin.nomDisque, win.taille(formatWin.disque.total || 0),
                               formatWin.disque.device || ""].filter(function(x) { return x !== "" }).join("  ·  ")
                        opacity: 0.75
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }
            }

            Kirigami.Separator { Layout.fillWidth: true }

            GridLayout {
                columns: 2
                columnSpacing: 14
                rowSpacing: 8
                Layout.fillWidth: true
                QQC2.Label { text: win.trad("File system"); opacity: 0.75 }
                QQC2.ComboBox {
                    id: formatCombo
                    Layout.fillWidth: true
                    enabled: !formatWin.enCours
                    model: formatWin.choix.map(function(c) { return c[1] })
                    onCurrentIndexChanged: nomVolume.text = nomVolume.text.slice(0, formatWin.choix[currentIndex][3])
                }
                Item { width: 1; height: 1 }
                QQC2.Label {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 2   // fixed height: the window does not move
                    wrapMode: Text.Wrap
                    opacity: 0.65
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    text: formatWin.manque === "" ? formatWin.choisi[2]
                          : win.trad("Not available on this computer (missing tool: {tool}).").replace("{tool}", formatWin.manque)
                    color: formatWin.manque === "" ? Kirigami.Theme.textColor : "#c42b1c"
                }
                QQC2.Label { text: win.trad("Volume name"); opacity: 0.75 }
                QQC2.TextField {
                    id: nomVolume
                    Layout.fillWidth: true
                    enabled: !formatWin.enCours
                    maximumLength: formatWin.choisi[3]
                }
                QQC2.Label { text: win.trad("Formatting options"); opacity: 0.75 }   // aligned with the other labels
                QQC2.CheckBox {
                    id: rapide
                    text: win.trad("Quick format")
                    checked: true
                    enabled: !formatWin.enCours
                }
            }

            Rectangle {   // warning, as a red banner like the Health tab
                Layout.fillWidth: true
                Layout.topMargin: 4
                implicitHeight: alerte.implicitHeight + 20
                radius: 6
                color: Qt.rgba(0.77, 0.17, 0.11, 0.10)
                border.width: 1
                border.color: Qt.rgba(0.77, 0.17, 0.11, 0.35)
                QQC2.Label {
                    id: alerte
                    anchors.fill: parent
                    anchors.margins: 10
                    wrapMode: Text.Wrap
                    text: win.trad("All the data on “{name}” will be permanently erased.").replace("{name}", formatWin.disque.label || "")
                    color: "#c42b1c"
                    font.bold: true
                }
            }

            // progress banner (always present: the window keeps its size)
            ColumnLayout {
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    QQC2.Label {
                        font.bold: formatWin.resultat !== ""
                        color: formatWin.resultat === "ok" ? "#2e9d4f"
                               : (formatWin.resultat !== "" ? "#c42b1c" : Kirigami.Theme.textColor)
                        text: formatWin.enCours ? win.trad("Formatting…")
                              : (formatWin.resultat === "ok" ? win.trad("Format complete.")
                              : (formatWin.resultat === "erreur" ? win.trad("Formatting failed.")
                              : (formatWin.resultat === "annule" ? win.trad("Format cancelled: the volume must be formatted again before use.")
                              : win.trad("Ready"))))
                        opacity: formatWin.enCours || formatWin.resultat !== "" ? 1 : 0.6
                    }
                    Item { Layout.fillWidth: true }
                    QQC2.Label {
                        visible: formatWin.enCours && formatWin.avance >= 0
                        text: Math.round(formatWin.avance * 100) + " %"
                    }
                }
                Rectangle {
                    id: pisteFormat
                    Layout.fillWidth: true
                    implicitHeight: 8
                    radius: 4
                    clip: true
                    color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                   Kirigami.Theme.textColor.b, 0.12)
                    Rectangle {   // known progress (full format) or result
                        height: parent.height
                        radius: 4
                        visible: formatWin.resultat !== "" || (formatWin.enCours && formatWin.avance >= 0)
                        width: formatWin.resultat !== "" ? parent.width : parent.width * Math.max(0, formatWin.avance)
                        color: formatWin.resultat === "erreur" || formatWin.resultat === "annule" ? "#c42b1c"
                               : (formatWin.resultat === "ok" ? "#2e9d4f" : win.gaugeBlue)
                        Behavior on width { NumberAnimation { duration: 800 } }
                    }
                    Rectangle {   // unknown progress (quick format): scrolling segment, like Windows
                        id: segment
                        height: parent.height
                        radius: 4
                        width: parent.width * 0.25
                        color: win.gaugeBlue
                        visible: formatWin.enCours && formatWin.avance < 0
                        SequentialAnimation on x {
                            running: segment.visible
                            loops: Animation.Infinite
                            NumberAnimation { from: -segment.width; to: pisteFormat.width; duration: 1400 }
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 6
                spacing: 8
                Item { Layout.fillWidth: true }
                QQC2.Button {
                    text: win.trad("Format")
                    enabled: !formatWin.enCours && formatWin.infosLues && formatWin.manque === ""
                    onClicked: {
                        formatWin.resultat = ""
                        formatWin.avance = -1
                        formatWin.annule = false
                        formatWin.complet = !rapide.checked
                        formatWin.enCours = true
                        backend.formatVolume(formatWin.disque.device, formatWin.choisi[0], nomVolume.text, rapide.checked)
                    }
                }
                QQC2.Button {
                    // quick format: a few seconds, cannot be interrupted; full: can be cancelled
                    text: formatWin.resultat === "ok" ? win.trad("Close") : win.trad("Cancel")
                    enabled: !formatWin.enCours || (formatWin.complet && !formatWin.annule)
                    onClicked: {
                        if (formatWin.enCours) {
                            formatWin.annule = true
                            backend.formatCancel(formatWin.disque.device)
                        } else {
                            formatWin.close()
                        }
                    }
                }
            }
        }
    }

    // options (2.0): small window in the same style as "About": single click (setting of all of Plasma)
    // and free space alert threshold
    Window {
        id: optionsDialog
        transientParent: win
        flags: Qt.Dialog | Qt.FramelessWindowHint
        modality: Qt.NonModal
        title: win.trad("Options")
        readonly property int ombre: 16
        width: 460 + 2 * ombre
        minimumWidth: width
        maximumWidth: width
        height: optionsCol.implicitHeight + 40 + 16 + 50 + 2 * ombre
        color: "transparent"

        Rectangle {
            anchors.fill: parent
            anchors.margins: optionsDialog.ombre
            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: "#55000000"
                shadowBlur: 0.8
                shadowVerticalOffset: 3
            }
            radius: 8
            color: Kirigami.Theme.backgroundColor
            border.width: 1
            border.color: Kirigami.Theme.textColor.hslLightness > 0.5 ? "#30ffffff" : "#30000000"
        }
        QQC2.Label {
            x: optionsDialog.ombre + 12
            y: optionsDialog.ombre + 8
            text: win.trad("Options")
        }
        function open() {
            maximumHeight = 100000
            minimumHeight = 0
            show()
            raise()
            requestActivate()
            Qt.callLater(function() {
                optionsDialog.minimumHeight = optionsDialog.height
                optionsDialog.maximumHeight = optionsDialog.height
            })
        }
        Shortcut {
            sequence: "Escape"
            onActivated: optionsDialog.close()
        }
        QQC2.Button {
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: optionsDialog.ombre + 16
            text: win.trad("Close")
            onClicked: optionsDialog.close()
        }
        ColumnLayout {
            id: optionsCol
            x: optionsDialog.ombre + 16
            y: optionsDialog.ombre + 44
            width: 460 - 32
            spacing: 6

            QQC2.CheckBox {
                text: win.trad("Open items with a single click")
                checked: backend.singleClick
                onToggled: backend.setSingleClick(checked)
            }
            QQC2.Label {
                Layout.fillWidth: true
                Layout.leftMargin: 28
                wrapMode: Text.Wrap
                opacity: 0.7
                font: Kirigami.Theme.smallFont
                text: win.trad("Applies to all of Plasma: Dolphin, the desktop and This PC.")
            }
            QQC2.CheckBox {
                Layout.topMargin: 10
                text: win.trad("Compact toolbar (icons only)")
                checked: backend.compactBar
                onToggled: backend.setCompactBar(checked)
            }
            QQC2.Label {
                Layout.fillWidth: true
                Layout.leftMargin: 28
                wrapMode: Text.Wrap
                opacity: 0.7
                font: Kirigami.Theme.smallFont
                text: win.trad("Same commands as the full toolbar, shown as icons. Choosing which sections to show is in the “…” menu.")
            }
            Kirigami.Separator {
                Layout.fillWidth: true
                Layout.topMargin: 10
                Layout.bottomMargin: 6
            }
            QQC2.Label { text: win.trad("Free space alert") }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                BleuCurseur {
                    // in the direction of the gauge: 20 % on the left, 10 % on the right, shown mirrored so that the
                    // fill starts from 10 % (on the right): at 20 %, the widest alert, the bar is full;
                    // custom slider (Basic style: the Plasma style keeps its handle in its own background and ignores the palette)
                    id: seuilCurseur
                    Layout.fillWidth: true
                    LayoutMirroring.enabled: true
                    from: 10
                    to: 20
                    stepSize: 1
                    snapMode: Basic.Slider.SnapAlways
                    value: backend.alertPercent
                    onMoved: backend.setAlertPercent(Math.round(value))
                }
                QQC2.Label {
                    text: win.trad("{n} %").replace("{n}", Math.round(seuilCurseur.value))
                    Layout.preferredWidth: 44
                    horizontalAlignment: Text.AlignRight
                }
            }
            RowLayout {   // the two ends of the slider, to read its direction at a glance
                Layout.fillWidth: true
                Layout.rightMargin: 54
                Layout.topMargin: -4
                QQC2.Label { text: win.trad("{n} %").replace("{n}", 20); font: Kirigami.Theme.smallFont; opacity: 0.7 }
                Item { Layout.fillWidth: true }
                QQC2.Label { text: win.trad("{n} %").replace("{n}", 10); font: Kirigami.Theme.smallFont; opacity: 0.7 }
            }
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                opacity: 0.7
                font: Kirigami.Theme.smallFont
                text: win.trad("The drive gauge turns red when the free space falls below this level.") + " "
                      + win.trad("Keeping 10 to 20 % free is recommended, especially for SSDs.")
            }
            Kirigami.Separator {
                Layout.fillWidth: true
                Layout.topMargin: 10
                Layout.bottomMargin: 6
            }
            // miseAJour (1.1): moved from "About" to the Options (2.0): it is a setting
            QQC2.CheckBox {
                Layout.fillWidth: true
                visible: !backend.storeListed   // 2.1: useless when Discover already warns about updates
                text: win.trad("Check for a new version once a week")
                checked: backend.updateCheck
                onToggled: backend.setUpdateCheck(checked)
            }
        }
    }

    // aProposDialog (2.0): real independent window, like Properties, Format and the printers
    Window {
        id: aboutDialog
        // 2.0: like the "About" of Fenestra Search and Menu 11: without a KWin title bar (so no
        // "Minimize" and no moving), title drawn inside the window, closing with
        // "Close" or Esc; attached to This PC, so that KWin centres it; size fixed below
        transientParent: win
        flags: Qt.Dialog | Qt.FramelessWindowHint
        modality: Qt.NonModal
        title: win.trad("About Fenestra This PC")
        // 2.0: rounded corners and shadow, like the other Fenestra "About" windows; the transparent
        // margin around the card leaves room for the shadow (KWin does not add one here)
        readonly property int ombre: 16
        width: 480 + 2 * ombre
        minimumWidth: width
        maximumWidth: width
        height: aboutCol.implicitHeight + 40 + 16 + 50 + 2 * ombre   // title + content + button + shadow
        color: "transparent"

        Rectangle {   // background with rounded corners + thin frame + shadow: the window stands out from the background
            anchors.fill: parent
            anchors.margins: aboutDialog.ombre
            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: "#55000000"
                shadowBlur: 0.8
                shadowVerticalOffset: 3
            }
            radius: 8
            color: Kirigami.Theme.backgroundColor
            border.width: 1
            border.color: Kirigami.Theme.textColor.hslLightness > 0.5 ? "#30ffffff" : "#30000000"
        }
        QQC2.Label {   // title drawn inside the window, like Search and Menu 11
            x: aboutDialog.ombre + 12
            y: aboutDialog.ombre + 8
            text: win.trad("About Fenestra This PC")
        }

        function open() {   // same call as before (aboutDialog.open())
            maximumHeight = 100000
            minimumHeight = 0
            show()
            raise()
            requestActivate()
            Qt.callLater(function() {
                aboutDialog.minimumHeight = aboutDialog.height
                aboutDialog.maximumHeight = aboutDialog.height
            })
        }

        Shortcut {
            sequence: "Escape"
            onActivated: aboutDialog.close()
        }

        QQC2.Button {   // "Close" anchored at the bottom of the window, like the other windows
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: aboutDialog.ombre + 16
            text: win.trad("Close")
            onClicked: aboutDialog.close()
        }

        ColumnLayout {
            id: aboutCol
            x: aboutDialog.ombre + 16
            y: aboutDialog.ombre + 40   // under the title
            width: 480 - 32
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                spacing: 14
                Kirigami.Icon {   // program icon; theme's "computer" icon if none is installed
                    source: aboutInfo.icon !== "" ? aboutInfo.icon : "computer"
                    Layout.preferredWidth: 56
                    Layout.preferredHeight: 56
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    QQC2.Label {   // product name, not translated, like the other Fenestra "About" windows
                        text: "Fenestra This PC"
                        font.pixelSize: 22
                        font.bold: true
                    }
                    QQC2.Label {
                        text: win.trad("Version {version}").replace("{version}", aboutInfo.version)
                        opacity: 0.75
                    }
                }
            }

            Kirigami.Separator { Layout.fillWidth: true }

            RowLayout {   // logoFenestra (2.0): the series logo in front of its presentation
                Layout.fillWidth: true
                spacing: 12
                Image {
                    visible: aboutInfo.logo !== ""
                    source: aboutInfo.logo
                    Layout.preferredWidth: 40
                    Layout.preferredHeight: 40
                    Layout.alignment: Qt.AlignVCenter
                    sourceSize.width: 40 * Screen.devicePixelRatio    // drawn at the exact size: sharp edges
                    sourceSize.height: 40 * Screen.devicePixelRatio
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    mipmap: false
                }
                QQC2.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: win.trad("Part of the Fenestra series, which gives KDE Plasma the look of a Windows 11 desktop.")
                }
            }
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: win.trad("Author: {author}").replace("{author}", aboutInfo.auteur)
            }
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                visible: aboutInfo.licence !== ""
                text: win.trad("License: {license}").replace("{license}", aboutInfo.licence)
            }
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                opacity: 0.75
                text: win.trad("This software is neither affiliated with nor endorsed by Microsoft Corporation. Windows is a trademark of Microsoft Corporation.")
            }
            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                opacity: 0.75
                text: win.trad("Command bar icons: Fluent UI System Icons, © Microsoft Corporation, MIT license.")
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // barreRuban: ribbon command bar (This PC 1.1: the simple mode was removed)
        Item {
            id: barRoot
            // couleursBarre: the theme's "Header" colours (like the Dolphin menu strip)
            Kirigami.Theme.colorSet: Kirigami.Theme.Header
            Kirigami.Theme.inherit: false
            Layout.fillWidth: true
            Layout.preferredHeight: backend.compactBar ? 48 : 118
            readonly property var sel: win.selEntry
            readonly property bool canOpen: sel !== null && sel.available !== false
            readonly property bool canProps: sel !== null
                && (sel.isNetwork !== true || sel.mounted === true || sel.total > 0)

            function runCmd(a) {
                if (a === "add")
                    addNetDialog.open()
                else if (a === "open") {
                    if (canOpen)
                        backend.openFolder(sel.mountpoint)
                } else if (a === "props") {
                    if (canProps)
                        win.showProps(sel)
                } else if (a === "refresh")
                    backend.refreshAll()
                else if (a === "settings")
                    backend.launchApp("systemsettings")
                else if (a === "info")
                    backend.launchApp("kinfocenter")
                else if (a === "about")
                    aboutDialog.open()
                else if (a === "options")
                    optionsDialog.open()
            }
            Rectangle {
                anchors.fill: parent
                color: Kirigami.Theme.backgroundColor
            }

            // ===== COMPACT BAR ===== (option: same commands as the ribbon, icons only;
            // the rest (network, display of the sections) is in the "…" menu)
            RowLayout {
                id: compactRow
                visible: backend.compactBar
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                spacing: 2
                Repeater {
                    model: [
                        { t: win.trad("Properties"), i: "props", a: "props" },
                        { t: win.trad("Open"), i: "open", a: "open" },
                        { t: win.trad("Refresh"), i: "refresh", a: "refresh" },
                        { sep: true },
                        { t: win.trad("Add a network location"), i: "network", a: "add" },
                        { sep: true },
                        { t: win.trad("System Settings"), i: "settings", a: "settings" },
                        { t: win.trad("System information"), i: "info", a: "info" },
                        { t: win.trad("Options"), i: "options", a: "options" },
                        { t: win.trad("About"), i: "about", a: "about" },
                        { sep: true },
                        { t: win.trad("More"), i: "more", a: "more" }
                    ]
                    delegate: Item {
                        id: cBtn
                        readonly property bool sep: modelData.sep === true
                        readonly property bool usable: sep ? false : (modelData.a === "open" ? barRoot.canOpen
                            : (modelData.a === "props" ? barRoot.canProps : true))
                        Layout.preferredWidth: sep ? 17 : 40
                        Layout.preferredHeight: 40
                        Layout.alignment: Qt.AlignVCenter
                        Rectangle {
                            visible: cBtn.sep
                            anchors.centerIn: parent
                            width: 1
                            height: 24
                            color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                           Kirigami.Theme.textColor.b, 0.2)
                        }
                        Rectangle {
                            anchors.fill: parent
                            radius: 5
                            visible: cBtn.usable && (cMouse.containsMouse || cMouse.pressed)
                            color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                           Kirigami.Theme.highlightColor.b, cMouse.pressed ? 0.28 : 0.14)
                            border.width: 1
                            border.color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                                  Kirigami.Theme.highlightColor.b, 0.5)
                        }
                        Kirigami.Icon {
                            visible: !cBtn.sep
                            anchors.centerIn: parent
                            width: 26
                            height: 26
                            source: cBtn.sep ? "" : (barIconsCompact[modelData.i] || "")
                            isMask: true
                            color: Kirigami.Theme.textColor
                            opacity: cBtn.usable ? 1.0 : 0.45
                        }
                        MouseArea {
                            id: cMouse
                            anchors.fill: parent
                            enabled: cBtn.usable
                            hoverEnabled: true
                            onClicked: {
                                if (modelData.a === "more")
                                    moreMenu.popup(cBtn, 0, cBtn.height)
                                else
                                    barRoot.runCmd(modelData.a)
                            }
                            QQC2.ToolTip.visible: containsMouse
                            QQC2.ToolTip.delay: 500
                            QQC2.ToolTip.text: cBtn.sep ? "" : modelData.t
                        }
                    }
                }
                Item { Layout.fillWidth: true }
            }
            // "…" menu: sections shown (those of the ribbon check boxes)
            QQC2.Menu {
                id: moreMenu
                onAboutToShow: {
                    miDossiers.checked = win.foldersShown
                    miDisques.checked = win.disksShown
                    miReseau.checked = win.netShown
                    miImprimantes.checked = win.printersShown
                }
                QQC2.MenuItem {
                    id: miDossiers
                    text: win.sectName(win.trad("Folders ({n})"))
                    checkable: true
                    onToggled: win.foldersShown = checked
                }
                QQC2.MenuItem {
                    id: miDisques
                    text: win.sectName(win.trad("Devices and drives ({n})"))
                    checkable: true
                    onToggled: win.disksShown = checked
                }
                QQC2.MenuItem {
                    id: miReseau
                    text: win.sectName(win.trad("Network locations ({n})"))
                    checkable: true
                    onToggled: win.netShown = checked
                }
                QQC2.MenuItem {
                    id: miImprimantes
                    text: win.sectName(win.trad("Printers and devices ({n})"))
                    checkable: true
                    onToggled: win.printersShown = checked
                }
            }

            // ===== RIBBON ===== (This PC 1.1: only mode of the bar; 2.0: default mode)
            ColumnLayout {
                visible: !backend.compactBar
                anchors.fill: parent
                spacing: 0

                // Ribbon groups
                RowLayout {
                    id: ribbonRow
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.topMargin: 6
                    Layout.leftMargin: 10
                    Layout.rightMargin: 10
                    Layout.bottomMargin: 4
                    spacing: 0

                    Repeater {
                        model: [
                            { g: win.trad("Location"), items: [
                                { t: win.trad("Properties"), i: "props", a: "props", w: 84 },
                                { t: win.trad("Open"), i: "open", a: "open", w: 72 },
                                { t: win.trad("Refresh"), i: "refresh", a: "refresh", w: 84 } ] },
                            { g: win.trad("Network"), items: [
                                { t: win.trad("Add a network location"), i: "add", a: "add", w: 112 } ] },
                            { g: win.trad("System"), items: [
                                { t: win.trad("System Settings"), i: "settings", a: "settings", w: 100 },
                                { t: win.trad("System information"), i: "info", a: "info", w: 100 },
                                { t: win.trad("Options"), i: "options", a: "options", w: 84 },
                                { t: win.trad("About"), i: "about", a: "about", w: 84 } ] }
                        ]
                        delegate: RowLayout {
                            id: grp
                            Layout.fillHeight: true
                            spacing: 0

                            ColumnLayout {
                                Layout.fillHeight: true
                                spacing: 2

                                Row {
                                    Layout.alignment: Qt.AlignHCenter
                                    spacing: 2
                                    Repeater {
                                        model: modelData.items
                                        delegate: Item {
                                            id: bBtn
                                            readonly property bool usable: modelData.a === "open" ? barRoot.canOpen
                                                : (modelData.a === "props" ? barRoot.canProps : true)
                                            width: modelData.w
                                            height: 84

                                            Rectangle {
                                                anchors.fill: parent
                                                radius: 5
                                                visible: bBtn.usable && (bMouse.containsMouse || bMouse.pressed)
                                                color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                                               Kirigami.Theme.highlightColor.b, bMouse.pressed ? 0.28 : 0.14)
                                                border.width: 1
                                                border.color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                                                      Kirigami.Theme.highlightColor.b, 0.5)
                                            }
                                            Item {
                                                id: bIcon
                                                x: (parent.width - width) / 2
                                                y: 8
                                                width: 32
                                                height: 32
                                                opacity: bBtn.usable ? 1.0 : 0.45
                                                readonly property string src: barIconsRuban[modelData.i] || modelData.i
                                                readonly property bool isFile: src.indexOf("file://") === 0
                                                Image {
                                                    anchors.fill: parent
                                                    visible: bIcon.isFile
                                                    source: bIcon.isFile ? bIcon.src : ""
                                                    sourceSize.width: 64
                                                    sourceSize.height: 64
                                                    fillMode: Image.PreserveAspectFit
                                                    smooth: true
                                                    mipmap: true
                                                }
                                                Kirigami.Icon {
                                                    anchors.fill: parent
                                                    visible: !bIcon.isFile
                                                    source: bIcon.isFile ? "" : bIcon.src
                                                }
                                            }
                                            QQC2.Label {
                                                x: 4
                                                y: 46
                                                width: parent.width - 8
                                                height: 34
                                                text: modelData.t
                                                wrapMode: Text.Wrap
                                                horizontalAlignment: Text.AlignHCenter
                                                verticalAlignment: Text.AlignTop
                                                elide: Text.ElideRight
                                                maximumLineCount: 2
                                                font: Kirigami.Theme.smallFont
                                                color: Kirigami.Theme.textColor
                                                opacity: bBtn.usable ? 1.0 : 0.45
                                            }
                                            MouseArea {
                                                id: bMouse
                                                anchors.fill: parent
                                                enabled: bBtn.usable
                                                hoverEnabled: true
                                                onClicked: barRoot.runCmd(modelData.a)
                                            }
                                        }
                                    }
                                }
                                Item { Layout.fillHeight: true }   // titresAlignes (1.1): group title at the bottom of the ribbon
                                QQC2.Label {
                                    Layout.alignment: Qt.AlignHCenter
                                    text: modelData.g
                                    font: Kirigami.Theme.smallFont
                                    opacity: 0.65
                                    color: Kirigami.Theme.textColor
                                }
                            }

                            Rectangle {
                                Layout.preferredWidth: 1
                                Layout.fillHeight: true
                                Layout.topMargin: 6
                                Layout.bottomMargin: 6
                                Layout.leftMargin: 8
                                Layout.rightMargin: 8
                                color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                               Kirigami.Theme.textColor.b, 0.2)
                            }
                        }
                    }

                    // groupeAfficher (This PC 1.1): one check box per section, unchecked = section hidden
                    ColumnLayout {
                        Layout.fillHeight: true
                        spacing: 2

                        // casesAfficher: 1st column Folders / Devices / Network, Printers to the right
                        // of Folders; the 1st row is level with the button icons (y = 8)
                        Item {
                            Layout.preferredHeight: 84
                            Layout.preferredWidth: casesGrid.implicitWidth + 8
                            Grid {
                                id: casesGrid
                                x: 4
                                y: 8
                                flow: Grid.TopToBottom
                                rows: 3
                                columnSpacing: 14
                                rowSpacing: 2
                                Repeater {
                                    model: [
                                        { t: win.trad("Folders ({n})"), k: "folders" },
                                        { t: win.trad("Devices and drives ({n})"), k: "disks" },
                                        { t: win.trad("Network locations ({n})"), k: "net" },
                                        { t: win.trad("Printers and devices ({n})"), k: "printers" }
                                    ]
                                    delegate: QQC2.CheckBox {
                                        topPadding: 0
                                        bottomPadding: 0
                                        font: Kirigami.Theme.smallFont
                                        text: win.sectName(modelData.t)
                                        checked: win[modelData.k + "Shown"]
                                        onToggled: win[modelData.k + "Shown"] = checked
                                    }
                                }
                            }
                        }
                        Item { Layout.fillHeight: true }   // titresAlignes (1.1)
                        QQC2.Label {
                            Layout.alignment: Qt.AlignHCenter
                            text: win.trad("Show")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.65
                            color: Kirigami.Theme.textColor
                        }
                    }
                    Rectangle {
                        Layout.preferredWidth: 1
                        Layout.fillHeight: true
                        Layout.topMargin: 6
                        Layout.bottomMargin: 6
                        Layout.leftMargin: 8
                        Layout.rightMargin: 8
                        color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                       Kirigami.Theme.textColor.b, 0.2)
                    }

                    Item { Layout.fillWidth: true }
                }
            }
        }
        Kirigami.Separator { Layout.fillWidth: true }

        // miseAJour (1.1): "new version available" banner (nothing installs by itself)
        Kirigami.InlineMessage {
            id: majBandeau
            property string version: ""
            property string adresse: ""
            Layout.fillWidth: true
            Layout.margins: 8
            type: Kirigami.MessageType.Information
            showCloseButton: true
            visible: false
            text: win.trad("Version {version} of This PC is available. To update, download it and run bash install.sh again.").replace("{version}", version)
            actions: [
                Kirigami.Action {
                    text: win.trad("View")
                    icon.name: "internet-web-browser"
                    onTriggered: Qt.openUrlExternally(majBandeau.adresse)
                }
            ]
            Connections {
                target: backend
                function onUpdateAvailable(version, adresse) {
                    majBandeau.version = version
                    majBandeau.adresse = adresse
                    majBandeau.visible = true
                }
            }
        }

        // Content area (Plasma "content" colours)
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Kirigami.Theme.colorSet: Kirigami.Theme.View
            Kirigami.Theme.inherit: false
            color: Kirigami.Theme.backgroundColor

            QQC2.ScrollView {
                id: scroll
                anchors.fill: parent
                contentWidth: availableWidth
                // Windows 11 style scroll bar, like Menu 11: invisible at rest, thin line
                // when the mouse moves, wider handle on hover
                QQC2.ScrollBar.vertical: QQC2.ScrollBar {
                    id: barre
                    parent: scroll
                    x: scroll.width - width
                    y: 0
                    height: scroll.height
                    implicitWidth: 14
                    hoverEnabled: true
                    policy: QQC2.ScrollBar.AsNeeded
                    visible: size < 1.0
                    readonly property bool survol: hovered || pressed
                    readonly property bool eveille: active || extinction.running
                    function reveil() { extinction.restart(); }
                    Timer { id: extinction; interval: 1500 }
                    background: Item {   // hover: narrow, discreet rail, no outline (like Menu 11)
                        opacity: barre.survol ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: 150 } }
                        Rectangle {   // stuck to the edge of the window, like the Start menu
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            width: 10
                            radius: 5
                            color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                           Kirigami.Theme.textColor.b, 0.07)
                        }
                    }
                    contentItem: Item {
                        implicitWidth: 14
                        Rectangle {   // mouse moving: thin line on the edge side
                            anchors.right: parent.right
                            anchors.rightMargin: 2   // same right edge as the handle: it widens without shifting
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            anchors.topMargin: 3
                            anchors.bottomMargin: 3
                            width: 2
                            radius: 1
                            color: Kirigami.Theme.textColor
                            opacity: (barre.eveille && !barre.survol) ? 0.55 : 0
                            Behavior on opacity { NumberAnimation { duration: 150 } }
                        }
                        Rectangle {   // hover / drag: handle, at the same right edge as the thin line
                            anchors.right: parent.right
                            anchors.rightMargin: 2
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            anchors.topMargin: 3
                            anchors.bottomMargin: 3
                            width: 6
                            radius: 3
                            color: Kirigami.Theme.textColor
                            opacity: barre.survol ? 0.45 : 0
                            Behavior on opacity { NumberAnimation { duration: 150 } }
                        }
                    }
                }
                HoverHandler { onPointChanged: barre.reveil() }   // the mouse moves: the bar wakes up

                ColumnLayout {
                    x: 10
                    width: scroll.availableWidth - 10
                    spacing: 0

                    // "Folders" section header
                    Item {
                        visible: win.foldersShown
                        Layout.fillWidth: true
                        Layout.leftMargin: 23
                        Layout.rightMargin: 15
                        Layout.topMargin: 12
                        Layout.bottomMargin: 4
                        implicitHeight: headerRow.implicitHeight

                        RowLayout {
                            id: headerRow
                            anchors.fill: parent
                            spacing: 4

                            Kirigami.Icon {
                                source: win.foldersCollapsed ? "arrow-up" : "arrow-down"
                                Layout.preferredWidth: 14
                                Layout.preferredHeight: 14
                            }
                            QQC2.Label {
                                text: win.trad("Folders ({n})").replace("{n}", foldersModel.length)
                                opacity: 0.75
                                Layout.rightMargin: 6
                            }
                            Kirigami.Separator { Layout.fillWidth: true }
                        }
                        MouseArea {   // flecheSeule: only the arrow collapses the section
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            width: 24
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.foldersCollapsed = !win.foldersCollapsed
                        }
                    }

                    // Folders grid
                    GridLayout {
                        id: folderGrid
                        visible: win.foldersShown && !win.foldersCollapsed
                        Layout.fillWidth: true
                        Layout.leftMargin: 15
                        Layout.rightMargin: 15
                        Layout.bottomMargin: 8
                        readonly property real cellWidth: Math.max(216, frameWidth)
                        columns: Math.max(1, Math.floor((scroll.availableWidth - 32) / (cellWidth + 8)))
                        columnSpacing: 8
                        rowSpacing: 6
                        uniformCellWidths: true

                        // cadreDossiers: width of the frame = margin + icon + space + longest name + margin
                        TextMetrics {
                            id: folderLongest
                            font: Kirigami.Theme.defaultFont
                            text: {
                                var m = ""
                                for (var i = 0; i < foldersModel.length; i++)
                                    if (foldersModel[i].label.length > m.length)
                                        m = foldersModel[i].label
                                return m
                            }
                        }
                        readonly property real frameWidth: 8 + 67 + 10 + Math.ceil(folderLongest.advanceWidth) + 16

                        Repeater {
                            model: foldersModel
                            delegate: Item {
                                Layout.fillWidth: false
                                Layout.preferredWidth: folderGrid.cellWidth
                                Layout.preferredHeight: 80

                                Row {
                                    anchors.left: parent.left
                                    anchors.leftMargin: 8
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 10

                                    Kirigami.Icon {
                                        source: modelData.icon
                                        width: 67; height: 67
                                    }
                                    QQC2.Label {
                                        text: modelData.label
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }
                                // Frame on hover (like Dolphin): Plasma accent colour, translucent
                                Rectangle {
                                    z: -1
                                    anchors.left: parent.left
                                    anchors.top: parent.top
                                    anchors.bottom: parent.bottom
                                    width: Math.min(parent.width, parent.parent.frameWidth)
                                    radius: 5
                                    visible: folderMouse.containsMouse || folderMouse.pressed
                                    color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                                   Kirigami.Theme.highlightColor.b, 0.12)
                                    border.width: 1
                                    border.color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                                          Kirigami.Theme.highlightColor.b, 0.35)
                                }
                                MouseArea {
                                    id: folderMouse
                                    anchors.left: parent.left
                                    anchors.top: parent.top
                                    anchors.bottom: parent.bottom
                                    width: Math.min(parent.width, parent.parent.frameWidth)
                                    hoverEnabled: true
                                    onPressed: win.selectedKey = ""
                                    onDoubleClicked: if (!backend.singleClick) backend.openFolder(modelData.path)
                                    onClicked: if (backend.singleClick) backend.openFolder(modelData.path)   // simpleClic
                                }
                            }
                        }
                    }

                    // "Devices and drives" section header
                    Item {
                        visible: win.disksShown
                        Layout.fillWidth: true
                        Layout.leftMargin: 23
                        Layout.rightMargin: 15
                        Layout.topMargin: 12
                        Layout.bottomMargin: 4
                        implicitHeight: disksHeaderRow.implicitHeight

                        RowLayout {
                            id: disksHeaderRow
                            anchors.fill: parent
                            spacing: 4

                            Kirigami.Icon {
                                source: win.disksCollapsed ? "arrow-up" : "arrow-down"
                                Layout.preferredWidth: 14
                                Layout.preferredHeight: 14
                            }
                            QQC2.Label {
                                text: win.trad("Devices and drives ({n})").replace("{n}", backend.disks.length)
                                opacity: 0.75
                                Layout.rightMargin: 6
                            }
                            Kirigami.Separator { Layout.fillWidth: true }
                        }
                        MouseArea {   // flecheSeule: only the arrow collapses the section
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            width: 24
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.disksCollapsed = !win.disksCollapsed
                        }
                    }

                    // Disk cards
                    GridLayout {
                        visible: win.disksShown && !win.disksCollapsed
                        Layout.fillWidth: true
                        Layout.leftMargin: 15
                        Layout.rightMargin: 15
                        Layout.topMargin: 16
                        Layout.bottomMargin: 8
                        columns: win.cardCols
                        columnSpacing: 8
                        rowSpacing: 8
                        uniformCellWidths: true

                        Repeater {
                            model: backend.disks
                            delegate: Item {
                                Layout.fillWidth: false
                                Layout.preferredWidth: win.cardWidth
                                Layout.preferredHeight: 88
                                property real frac: modelData.total > 0 ? modelData.used / modelData.total : 0

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 8
                                    anchors.rightMargin: 8
                                    spacing: 10

                                    Item {   // icon of the disks and drives: 56 px (icon of the icon theme, we do not redraw it)
                                        Layout.preferredWidth: 56
                                        Layout.preferredHeight: 56
                                        Layout.alignment: Qt.AlignVCenter
                                        Kirigami.Icon {
                                            anchors.centerIn: parent
                                            source: modelData.icon
                                            width: 56
                                            height: width
                                        }
                                        // cadenas (2.0): encrypted disk, simple visual mark without action
                                        Image {
                                            visible: modelData.encrypted === true && source !== ""
                                            source: barIconsRuban.lock || ""
                                            width: 30; height: 30     // vector drawing rasterized at the exact size: sharp edges
                                            sourceSize.width: 30 * Screen.devicePixelRatio
                                            sourceSize.height: 30 * Screen.devicePixelRatio
                                            smooth: true
                                            mipmap: false
                                            anchors.right: parent.right
                                            anchors.top: parent.top
                                            anchors.rightMargin: 0    // right edge of the disk
                                            anchors.topMargin: -3     // base of the padlock on the top of the front face
                                        }
                                        // santeDisque (1.1): orange dot (wear) or red (failing)
                                        Image {
                                            readonly property string src: modelData.health ? (badgeHealth[modelData.health] || "") : ""
                                            visible: src !== ""
                                            source: src
                                            width: 20; height: 20
                                            sourceSize.width: 80
                                            sourceSize.height: 80
                                            smooth: true
                                            mipmap: true
                                            anchors.right: parent.right
                                            anchors.bottom: parent.bottom
                                            anchors.rightMargin: -2
                                            anchors.bottomMargin: -2
                                        }
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Layout.alignment: Qt.AlignVCenter
                                        spacing: 5

                                        QQC2.Label { text: modelData.label; Layout.bottomMargin: 2; Layout.fillWidth: true }   // texteLecteur

                                        Rectangle {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 16
                                            visible: !(modelData.isOptical === true && modelData.total === 0)
                                            // jaugeGrise: light grey gauge background (16 % of the text colour over the background)
                                            color: Qt.tint(Kirigami.Theme.backgroundColor,
                                                           Qt.rgba(Kirigami.Theme.textColor.r,
                                                                   Kirigami.Theme.textColor.g,
                                                                   Kirigami.Theme.textColor.b, 0.16))
                                            border.width: 1
                                            border.color: Qt.rgba(Kirigami.Theme.textColor.r,
                                                                  Kirigami.Theme.textColor.g,
                                                                  Kirigami.Theme.textColor.b, 0.38)
                                            Rectangle {
                                                x: 1; y: 1
                                                width: (parent.width - 2) * frac
                                                height: parent.height - 2
                                                color: (frac < win.alertFrac || modelData.isOptical === true) ? win.gaugeBlue
                                                                  : win.gaugeRed
                                            }
                                        }

                                        QQC2.Label {
                                            text: modelData.freeText
                                            font: Kirigami.Theme.smallFont
                                            opacity: 0.75
                                            Layout.fillWidth: true
                                        }
                                        QQC2.Label {   // santeDisque (1.1)
                                            visible: text !== ""
                                            text: modelData.healthText || ""
                                            font.pointSize: Kirigami.Theme.smallFont.pointSize
                                            font.weight: Font.DemiBold
                                            color: modelData.health === "fail" ? "#c42b1c" : "#d35400"
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                }
                                // Thin frame on hover; on click, background tinted with the accent colour (stays until another click)
                                Rectangle {
                                    z: -1
                                    anchors.fill: parent
                                    radius: 4
                                    readonly property bool selected: win.selectedKey === modelData.mountpoint
                                    visible: selected || cardMouse.containsMouse
                                    color: (selected || cardMouse.pressed)
                                           ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                                     Kirigami.Theme.highlightColor.b, 0.22)
                                           : "transparent"
                                    border.width: 1
                                    border.color: selected
                                                  ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                                            Kirigami.Theme.highlightColor.b, 0.8)
                                                  : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                                            Kirigami.Theme.textColor.b, 0.55)
                                }
                                MouseArea {
                                    id: cardMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                                    onPressed: win.selectedKey = modelData.mountpoint
                                    onDoubleClicked: function(mouse) { if (!backend.singleClick) ouvrir(mouse) }
                                    function ouvrir(mouse) {   // simpleClic: double click, or single click depending on Plasma
                                        if (mouse.button === Qt.LeftButton)
                                            backend.openFolder(modelData.mountpoint)
                                    }
                                    onClicked: function(mouse) {
                                        if (backend.singleClick) ouvrir(mouse)
                                        if (mouse.button === Qt.RightButton) {
                                            win.menuDisk = modelData
                                            diskMenu.popup()
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // "Network locations" section header
                    Item {
                        visible: win.netShown
                        Layout.fillWidth: true
                        Layout.leftMargin: 23
                        Layout.rightMargin: 15
                        Layout.topMargin: 12
                        Layout.bottomMargin: 4
                        implicitHeight: netHeaderRow.implicitHeight

                        RowLayout {
                            id: netHeaderRow
                            anchors.fill: parent
                            spacing: 4

                            Kirigami.Icon {
                                source: win.netCollapsed ? "arrow-up" : "arrow-down"
                                Layout.preferredWidth: 14
                                Layout.preferredHeight: 14
                            }
                            QQC2.Label {
                                text: win.trad("Network locations ({n})").replace("{n}", backend.networks.length)
                                opacity: 0.75
                                Layout.rightMargin: 6
                            }
                            Kirigami.Separator { Layout.fillWidth: true }
                        }
                        MouseArea {   // flecheSeule: only the arrow collapses the section
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            width: 24
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.netCollapsed = !win.netCollapsed
                        }
                    }

                    // Network location cards: mounted shares and locations added by the user
                    GridLayout {
                        visible: win.netShown && !win.netCollapsed
                        Layout.fillWidth: true
                        Layout.leftMargin: 15
                        Layout.rightMargin: 15
                        Layout.bottomMargin: 8
                        columns: win.cardCols
                        columnSpacing: 8
                        rowSpacing: 8
                        uniformCellWidths: true

                        Repeater {
                            model: backend.networks
                            delegate: Item {
                                Layout.fillWidth: false
                                Layout.preferredWidth: win.cardWidth
                                Layout.preferredHeight: 88
                                property real frac: modelData.total > 0 ? modelData.used / modelData.total : 0
                                readonly property bool offline: modelData.available === false

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 8
                                    anchors.rightMargin: 8
                                    spacing: 10

                                    Item {
                                        Layout.preferredWidth: 48
                                        Layout.preferredHeight: 48
                                        Layout.alignment: Qt.AlignVCenter
                                        Kirigami.Icon {
                                            anchors.centerIn: parent
                                            source: modelData.icon
                                            width: 48
                                            height: 48
                                            opacity: offline ? 0.4 : 1.0
                                        }
                                        // Red dot: server not reachable
                                        // pastilleSvg: sharp vector image
                                        Image {
                                            visible: offline && badgeOffline !== ""
                                            source: badgeOffline
                                            width: 20; height: 20
                                            sourceSize.width: 80
                                            sourceSize.height: 80
                                            smooth: true
                                            mipmap: true
                                            anchors.right: parent.right
                                            anchors.bottom: parent.bottom
                                            anchors.rightMargin: -2
                                            anchors.bottomMargin: -2
                                        }
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Layout.alignment: Qt.AlignVCenter
                                        spacing: 5

                                        QQC2.Label {
                                            text: modelData.label
                                            Layout.bottomMargin: 2
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                            opacity: offline ? 0.6 : 1.0
                                        }

                                        Rectangle {
                                            visible: modelData.total > 0
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 16
                                            // jaugeGrise: light grey gauge background (16 % of the text colour over the background)
                                            color: Qt.tint(Kirigami.Theme.backgroundColor,
                                                           Qt.rgba(Kirigami.Theme.textColor.r,
                                                                   Kirigami.Theme.textColor.g,
                                                                   Kirigami.Theme.textColor.b, 0.16))
                                            border.width: 1
                                            border.color: Qt.rgba(Kirigami.Theme.textColor.r,
                                                                  Kirigami.Theme.textColor.g,
                                                                  Kirigami.Theme.textColor.b, 0.38)
                                            Rectangle {
                                                x: 1; y: 1
                                                width: (parent.width - 2) * frac
                                                height: parent.height - 2
                                                color: (frac < win.alertFrac || modelData.isOptical === true) ? win.gaugeBlue
                                                                  : win.gaugeRed
                                            }
                                        }

                                        QQC2.Label {
                                            text: modelData.freeText
                                            font: Kirigami.Theme.smallFont
                                            opacity: offline ? 0.6 : 0.75
                                        }
                                    }
                                }

                                // Thin frame on hover; on click, background tinted with the accent colour (stays until another click)
                                Rectangle {
                                    z: -1
                                    anchors.fill: parent
                                    radius: 4
                                    readonly property bool selected: win.selectedKey === modelData.mountpoint
                                    visible: selected || cardMouse.containsMouse
                                    color: (selected || cardMouse.pressed)
                                           ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                                     Kirigami.Theme.highlightColor.b, 0.22)
                                           : "transparent"
                                    border.width: 1
                                    border.color: selected
                                                  ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                                            Kirigami.Theme.highlightColor.b, 0.8)
                                                  : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                                            Kirigami.Theme.textColor.b, 0.55)
                                }
                                MouseArea {
                                    id: cardMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                                    onPressed: win.selectedKey = modelData.mountpoint
                                    onDoubleClicked: function(mouse) { if (!backend.singleClick) ouvrir(mouse) }
                                    function ouvrir(mouse) {   // simpleClic: double click, or single click depending on Plasma
                                        if (mouse.button !== Qt.LeftButton)
                                            return
                                        if (offline) {
                                            msgDialog.title = win.trad("Location unavailable")
                                            msgLabel.text = win.trad("“{name}” is not responding. Check that it is turned on and connected to the network.").replace("{name}", modelData.label)
                                            msgDialog.open()
                                        } else {
                                            backend.openFolder(modelData.mountpoint)
                                        }
                                    }
                                    onClicked: function(mouse) {
                                        if (backend.singleClick) ouvrir(mouse)
                                        if (mouse.button === Qt.RightButton)
                                            netMenu.popup()
                                    }
                                }
                                QQC2.Menu {
                                    id: netMenu
                                    QQC2.MenuItem {
                                        text: win.trad("Open with Dolphin")
                                        icon.name: "folder-open"
                                        enabled: !offline
                                        onTriggered: backend.openFolder(modelData.mountpoint)
                                    }
                                    QQC2.MenuItem {
                                        text: win.trad("Properties")
                                        icon.name: "document-properties"
                                        visible: modelData.mounted === true || modelData.total > 0
                                        height: visible ? implicitHeight : 0
                                        onTriggered: win.showProps(modelData)
                                    }
                                    QQC2.MenuItem {
                                        text: win.trad("Remove this location")
                                        icon.name: "list-remove"
                                        visible: modelData.configured === true
                                        height: visible ? implicitHeight : 0
                                        onTriggered: backend.removeNetwork(modelData.url)
                                    }
                                }
                            }
                        }
                    }

                    // Printers and devices: information only, under the network locations
                    Item {
                        visible: win.printersShown && backend.printers.length > 0
                        Layout.fillWidth: true
                        Layout.leftMargin: 23
                        Layout.rightMargin: 15
                        Layout.topMargin: 12
                        Layout.bottomMargin: 4
                        implicitHeight: printersHeaderRow.implicitHeight

                        RowLayout {
                            id: printersHeaderRow
                            anchors.fill: parent
                            spacing: 4

                            Kirigami.Icon {
                                source: win.printersCollapsed ? "arrow-up" : "arrow-down"
                                Layout.preferredWidth: 14
                                Layout.preferredHeight: 14
                            }
                            QQC2.Label {
                                text: win.trad("Printers and devices ({n})").replace("{n}", backend.printers.length)
                                opacity: 0.75
                                Layout.rightMargin: 6
                            }
                            Kirigami.Separator { Layout.fillWidth: true }
                        }
                        MouseArea {   // flecheSeule: only the arrow collapses the section
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            width: 24
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.printersCollapsed = !win.printersCollapsed
                        }
                    }

                    GridLayout {
                        visible: win.printersShown && backend.printers.length > 0 && !win.printersCollapsed
                        Layout.fillWidth: true
                        Layout.leftMargin: 15
                        Layout.rightMargin: 15
                        Layout.bottomMargin: 8
                        columns: win.cardCols
                        columnSpacing: 8
                        rowSpacing: 8
                        uniformCellWidths: true

                        Repeater {
                            model: backend.printers
                            delegate: Item {
                                Layout.fillWidth: false
                                Layout.preferredWidth: win.cardWidth
                                Layout.preferredHeight: 72
                                opacity: modelData.offline ? 0.5 : 1.0

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 8
                                    anchors.rightMargin: 8
                                    spacing: 10

                                    Item {
                                        Layout.preferredWidth: 48
                                        Layout.preferredHeight: 48
                                        Layout.alignment: Qt.AlignVCenter
                                        Kirigami.Icon {
                                            anchors.centerIn: parent
                                            source: modelData.icon
                                            width: 48
                                            height: 48
                                        }
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Layout.alignment: Qt.AlignVCenter
                                        spacing: 3

                                        QQC2.Label {
                                            text: modelData.label
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }
                                        QQC2.Label {
                                            text: modelData.statusText
                                            font: Kirigami.Theme.smallFont
                                            opacity: 0.75
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }
                                        QQC2.Label {   // alerteEncre: fixed red text (no blinking), colours under the low threshold
                                            readonly property string basNoms: {
                                                var l = modelData.inks || []
                                                var t = []
                                                for (var i = 0; i < l.length; i++)
                                                    if (l[i].low)
                                                        t.push(l[i].name)
                                                return t.join(", ")
                                            }
                                            visible: !modelData.offline && basNoms !== ""
                                            text: win.trad("Low ink level: {colors}").replace("{colors}", basNoms)
                                            font.pointSize: Kirigami.Theme.smallFont.pointSize
                                            color: Kirigami.Theme.negativeTextColor
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }
                                    }
                                    // encreCarte: one vertical bar per cartridge, in its colour, filled from the bottom;
                                    // red outline under the low threshold; the tooltip gives the exact percentages
                                    Row {
                                        id: encreCarte
                                        visible: !modelData.offline && (modelData.inks || []).length > 0
                                        Layout.alignment: Qt.AlignVCenter
                                        spacing: 3
                                        HoverHandler { id: encreSurvol }
                                        QQC2.ToolTip.visible: encreSurvol.hovered && encreCarte.visible
                                        QQC2.ToolTip.delay: 400
                                        QQC2.ToolTip.text: {
                                            var t = []
                                            var l = modelData.inks || []
                                            for (var i = 0; i < l.length; i++)
                                                t.push(l[i].name + " : " + l[i].level + " %")
                                            return t.join("\n")
                                        }
                                        Repeater {
                                            model: modelData.inks || []
                                            delegate: Rectangle {
                                                // column with square corners (like the squares of the surface analysis):
                                                // clearly visible hollow; the "low level" alert is in the text of the card
                                                width: 8
                                                height: 50
                                                radius: 0
                                                color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                                               Kirigami.Theme.textColor.b, 0.10)
                                                border.width: 1
                                                border.color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                                                      Kirigami.Theme.textColor.b, 0.35)
                                                Rectangle {
                                                    anchors.bottom: parent.bottom
                                                    anchors.left: parent.left
                                                    anchors.right: parent.right
                                                    anchors.margins: parent.border.width
                                                    radius: 0
                                                    height: modelData.level > 0
                                                            ? Math.max(2, (parent.height - 2 * parent.border.width) * modelData.level / 100) : 0
                                                    color: modelData.color
                                                }
                                            }
                                        }
                                    }
                                }
                                Rectangle {
                                    z: -1
                                    anchors.fill: parent
                                    radius: 4
                                    readonly property bool selected: win.selectedKey === modelData.key
                                    visible: selected || printerMouse.containsMouse
                                    color: (selected || printerMouse.pressed)
                                           ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                                     Kirigami.Theme.highlightColor.b, 0.22)
                                           : "transparent"
                                    border.width: 1
                                    border.color: selected
                                                  ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                                            Kirigami.Theme.highlightColor.b, 0.8)
                                                  : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                                            Kirigami.Theme.textColor.b, 0.55)
                                }
                                MouseArea {
                                    id: printerMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                                    onPressed: win.selectedKey = modelData.key
                                    onDoubleClicked: function(mouse) { if (!backend.singleClick) ouvrir(mouse) }
                                    function ouvrir(mouse) {   // simpleClic: double click, or single click depending on Plasma
                                        if (mouse.button !== Qt.LeftButton)
                                            return
                                        if (modelData.scanner === true)   // scannersInfo (1.1): scan with Skanpage
                                            backend.launchApp("skanpage")
                                        else if (!modelData.indicator)
                                            win.showPrinter(modelData)
                                    }
                                    onClicked: function(mouse) {
                                        if (backend.singleClick) ouvrir(mouse)
                                        if (mouse.button === Qt.RightButton && !modelData.indicator)
                                            printerMenu.popup()
                                    }
                                }
                                QQC2.Menu {
                                    id: printerMenu
                                    QQC2.MenuItem {
                                        text: win.trad("Properties")
                                        icon.name: "document-properties"
                                        onTriggered: win.showPrinter(modelData)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // clicVideSelection: a click next to a disk removes its selection.
            // Transparent area above the content; it does not accept the click, which therefore continues
            // to the element below (which selects itself again if it is the target).
            MouseArea {
                z: 10
                anchors.fill: parent
                onPressed: function(mouse) {
                    win.selectedKey = ""
                    mouse.accepted = false
                }
            }
        }

        // Footer
        Kirigami.Separator { Layout.fillWidth: true }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 29
            color: Kirigami.Theme.backgroundColor

            QQC2.Label {
                anchors.left: parent.left
                anchors.leftMargin: 15
                anchors.verticalCenter: parent.verticalCenter
                opacity: 0.75
                text: win.trad("{n} item(s)").replace("{n}", (win.foldersCollapsed || !win.foldersShown ? 0 : foldersModel.length) + (win.disksCollapsed || !win.disksShown ? 0 : backend.disks.length) + (win.netCollapsed || !win.netShown ? 0 : backend.networks.length) + (win.printersCollapsed || !win.printersShown ? 0 : backend.printers.length))
            }
        }
    }
}
"""


BAR_ICONS = ("folder-network", "folder-open", "document-properties", "view-refresh",
             "preferences-system", "help-about")


def bar_icons():
    """Icônes de la barre de commandes : fichier du thème Fenestra s'il existe, sinon nom d'icône standard."""
    res = {}
    for name in BAR_ICONS:
        res[name] = name
        for base in (os.path.expanduser("~/.local/share/icons/windows-modern/places"),
                     "/usr/share/icons/windows-modern/places"):
            p = os.path.join(base, name + ".svg")
            if os.path.isfile(p):
                res[name] = "file://" + p
                break
    return res


APP_VERSION = '2.1'
APP_AUTEUR = 'naykkalak'
APP_LICENCE = 'GPL-3.0-or-later'


# logoFenestra (2.0): series logo, shipped with This PC (icons/fenestra-logo.svg)
def logo_fenestra():
    p = os.path.join(ICONS_DIR, "fenestra-logo.svg")
    return p if os.path.isfile(p) else ""


def about_info():
    """Informations affichées par « À propos de Ce PC »."""
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    icone = os.path.join(ICON_CACHE_DIR, "app-icon.png")   # program icon (otherwise the theme's)
    logo = logo_fenestra()
    return {"version": APP_VERSION, "auteur": APP_AUTEUR, "licence": {"GPL-3.0-or-later": tr("GPL-3.0 or later"), "GPL-3.0-only": tr("GPL-3.0 only")}.get(APP_LICENCE, APP_LICENCE),   # licenceTraduite
            "qt": QT_VERSION_STR, "pyqt": PYQT_VERSION_STR,
            "python": "%d.%d.%d" % sys.version_info[:3],
            "icon": ("file://" + icone) if os.path.isfile(icone) else "",
            "logo": ("file://" + logo) if logo else ""}


def bar_icons_ruban():
    """Icônes du mode complet de la barre (rubanWindows) : jeu coloré windows-modern, sinon icône du thème."""
    svg = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">\n<path d="M12 4.5C16.1421 4.5 19.5 7.85786 19.5 12C19.5 16.1421 16.1421 19.5 12 19.5C7.85786 19.5 4.5 16.1421 4.5 12C4.5 11.6236 4.52772 11.2538 4.58123 10.8923C4.64845 10.4382 4.31609 10 3.85708 10C3.48623 10 3.161 10.2562 3.10471 10.6228C3.03576 11.0718 3 11.5317 3 12C3 16.9706 7.02944 21 12 21C16.9706 21 21 16.9706 21 12C21 7.02944 16.9706 3 12 3C9.69494 3 7.59227 3.86656 6 5.29168V4.25C6 3.83579 5.66421 3.5 5.25 3.5C4.83579 3.5 4.5 3.83579 4.5 4.25V7.25C4.5 7.66421 4.83579 8 5.25 8H8.25C8.66421 8 9 7.66421 9 7.25C9 6.83579 8.66421 6.5 8.25 6.5H6.90093C8.23907 5.25883 10.0309 4.5 12 4.5Z" fill="#1e88e5"/>\n</svg>\n'
    actualiser = ""
    chemin = os.path.join(os.path.expanduser("~/.cache/fenestra"), "ce-pc-actualiser.svg")
    try:
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        if not os.path.isfile(chemin) or open(chemin, encoding="utf-8").read() != svg:
            with open(chemin, "w", encoding="utf-8") as f:
                f.write(svg)
        actualiser = "file://" + chemin
    except OSError:
        pass
    voulus = (("props", "mimetypes/text-x-generic", "text-x-generic"),
              ("open", "places/folder-open", "folder-open"),
              ("add", "places/folder-network-open", "folder-network"),
              ("settings", "apps/preferences-system", "preferences-system"),
              ("info", "devices/computer", "computer"),
              ("about", "apps/help-about", "help-about"),
              ("options", "actions/configure", "configure"))
    res = {"refresh": actualiser or "view-refresh"}
    # options (2.0): icon drawn for Fenestra (no coloured "options" icon in windows-modern)
    opt = os.path.join(ICONS_DIR, "fenestra-options.svg")
    for cle, sous, repli in voulus:
        res[cle] = repli
        for base in (os.path.expanduser("~/.local/share/icons/windows-modern"),
                     "/usr/share/icons/windows-modern"):
            p = os.path.join(base, sous + ".svg")
            if os.path.isfile(p):
                res[cle] = "file://" + p
                break
    if os.path.isfile(opt):
        res["options"] = "file://" + opt
    # encrypted disk (2.0): open padlock (unlocked volume), drawn for Fenestra; same drawing for all themes
    cad = ""
    for chemin in (os.path.join(ICONS_DIR, "fenestra-cadenas.svg"),
                   os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "fenestra-cadenas.svg")):
        if os.path.isfile(chemin):
            cad = chemin
            break
    res["lock"] = "file://" + cad if cad else ""
    # "About": same glossy style as "Options", drawn for Fenestra
    apr = os.path.join(ICONS_DIR, "fenestra-apropos.svg")
    if os.path.isfile(apr):
        res["about"] = "file://" + apr
    return res


def bar_icons_compact():
    """Icônes de la barre compacte (style Fluent, monochromes, recolorées par le thème)."""
    res = {}
    for base in (os.path.join(ICONS_DIR, "compact"),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "compact")):
        if os.path.isdir(base):
            for nom in ("props", "open", "refresh", "network", "settings", "info", "options", "about", "more"):
                p = os.path.join(base, nom + ".svg")
                if os.path.isfile(p):
                    res[nom] = "file://" + p
            break
    return res


WIN_CONFIG = os.path.join(CONFIG_DIR, "ce-pc-fenetre.json")   # fenetreMemo


def load_window_state():
    """Dernière taille de la fenêtre : {"w", "h", "max"}. Valeurs à 0 si inconnues ou invalides."""
    etat = {"w": 0, "h": 0, "max": False}
    try:
        with open(WIN_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        w, h = int(data.get("w", 0)), int(data.get("h", 0))
        geo = QApplication.primaryScreen().availableGeometry()
        if 640 <= w <= max(640, geo.width()) and 460 <= h <= max(460, geo.height()):
            etat["w"], etat["h"] = w, h
        etat["max"] = bool(data.get("max", False))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return etat


def main():
    app = QApplication(sys.argv)
    # Wayland: links the window to the launcher fenestra-ce-pc.desktop (window icon)
    app.setDesktopFileName("fenestra-ce-pc")
    theme = get_icon_theme_name()
    QIcon.setThemeName(theme)
    for names in DISK_ICON_LISTS:       # main thread only, see disk_icon()
        disk_icon(names)

    app_icon = os.path.join(ICON_CACHE_DIR, "app-icon.png")
    if os.path.isfile(app_icon):
        app.setWindowIcon(QIcon(app_icon))

    folders = list_user_folders()
    print(f"[ce_pc_plasma] thème d'icônes : {theme}")
    print("[ce_pc_plasma] icônes dossiers :",
          {f["label"]: f["icon"] for f in folders})

    backend = Backend()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("foldersModel", folders)
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("barIcons", bar_icons())
    engine.rootContext().setContextProperty("trTable", table_traductions(QML))
    engine.rootContext().setContextProperty("trDecimal", DECIMAL)
    engine.rootContext().setContextProperty("barIconsRuban", bar_icons_ruban())
    engine.rootContext().setContextProperty("barIconsCompact", bar_icons_compact())
    engine.rootContext().setContextProperty("aboutInfo", about_info())
    engine.rootContext().setContextProperty("sectInit", load_sections())
    engine.rootContext().setContextProperty("winInit", load_window_state())
    engine.rootContext().setContextProperty("badgeOffline", badge_offline())
    engine.rootContext().setContextProperty("badgeHealth", badge_health())
    engine.loadData(QML.encode("utf-8"))
    if not engine.rootObjects():
        print("Échec du chargement QML")
        return 1
    rc = app.exec()
    # Background threads (network, scanners, update check) may still be running:
    # leave at once instead of tearing Qt down under them (crash on close).
    # Settings are already saved and the caches are written atomically.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)


if __name__ == "__main__":
    sys.exit(main())
