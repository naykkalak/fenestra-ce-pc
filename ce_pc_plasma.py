#!/usr/bin/env python3
# Fenestra This PC — part of the Fenestra series by naykkalak
# https://github.com/naykkalak/fenestra-ce-pc
# Copyright (C) 2026 naykkalak
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ce PC (version Plasma/QML) - suit automatiquement les couleurs de Plasma."""

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

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtWidgets import QApplication

# fenestraLangues : traductions au format gettext (un dossier par langue), l'anglais sert de repli
import gettext

LANG_DIR = os.path.expanduser("~/.local/share/fenestra/ce-pc-langues")
LANG_ALIAS = {"pt": "pt_BR", "pt_PT": "pt_BR", "zh": "zh_CN", "zh_SG": "zh_CN",
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
        from PyQt6.QtCore import QLocale
        brut = [l.replace("-", "_") for l in QLocale().uiLanguages()]
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


# (libellé, type xdg-user-dir, nom de repli, noms d'icônes à essayer)
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
    "/sys", "/proc", "/dev", "/snap", "/boot/efi", "/tmp",
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


def get_base_disk_device(device):
    base = os.path.basename(device)
    m = re.match(r"^(sd[a-z]+|nvme\d+n\d+|mmcblk\d+)", base)
    return "/dev/" + m.group(1) if m else device


def is_removable(device):
    try:
        base = os.path.basename(device)
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


def disk_icon(names):
    for n in names:
        manual = os.path.join(ICON_CACHE_DIR, f"{n}.png")
        if os.path.isfile(manual):
            return "file://" + manual
    for n in names:
        if QIcon.hasThemeIcon(n):
            return n
    return names[-1]


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
        })
    return res


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
        elif removable:
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
        })

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
_ESPACE = {}          # url -> (heure, valeur ou None) : évite d'interroger le NAS toutes les 3 secondes
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


BAR_CONFIG = os.path.join(CONFIG_DIR, "ce-pc-barre.json")


def load_bar_mode():
    """Mode de la barre de commandes retenu : « simple » (par défaut) ou « complet »."""
    try:
        with open(BAR_CONFIG, encoding="utf-8") as f:
            mode = json.load(f).get("mode")
        return mode if mode in ("simple", "complet") else "simple"
    except Exception:
        return "simple"


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


_RESEAU_ECRIT = None   # cachesEcriture : dernier contenu écrit, pour ne réécrire que si la liste change


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
    if uri.startswith("usb:"):   # imprimantesUsb : sans le numéro de série
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
            seuil = 0
        couleur = couleurs[i] if i < len(couleurs) and re.fullmatch(r"#[0-9A-Fa-f]{6}", couleurs[i]) else "#808080"
        res.append({"name": n, "color": couleur, "level": niveau, "low": niveau <= seuil})
    return res


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
            injoignable = (cible is not None and ok is False)
        etat = tr("Offline") if injoignable else etats[nom]
        texte = etat + ((" · " + tr("Default")) if nom == defaut else "")
        peripherique = libelle_connexion(uri)
        fiche.update({"label": nom.replace("_", " "), "device": peripherique, "default": nom == defaut})
        connues[nom] = fiche
        res.append({"key": "printer:" + nom, "label": nom.replace("_", " "), "name": nom,
                    "icon": disk_icon(["printer"]), "statusText": texte,
                    "device": peripherique, "inks": [] if injoignable else niveaux_encre(nom, cible),
                    "offline": injoignable, "absent": False})
    for nom, fiche in list(connues.items()):
        if nom in vus:
            continue
        texte = tr("Offline") + ((" · " + tr("Default")) if fiche.get("default") else "")
        res.append({"key": "printer:" + nom, "label": fiche.get("label") or nom.replace("_", " "), "name": nom,
                    "icon": disk_icon(["printer"]), "statusText": texte,
                    "device": fiche.get("device", ""), "inks": [], "offline": True, "absent": True})
    enregistrer_imprimantes_connues(connues)
    return res


_PERIPH_ETAT = [None, []]   # peripheriquesInfo : dernière liste lue (relue seulement si /proc/bus/input/devices change)
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
    # peripheriquesSouris : le « clavier » d'une souris (nom = nom de la souris + « Keyboard ») n'est pas un vrai clavier
    for (groupe, genre), d in list(trouves.items()):
        if genre == "keyboard" and any(g == groupe and gen == "mouse" and d["name"].startswith(s["name"])
                                       for (g, gen), s in trouves.items()):
            del trouves[(groupe, genre)]
    liste = sorted(trouves.values(), key=lambda d: (_PERIPH_ORDRE[d["_genre"]], d["label"]))
    for d in liste:
        del d["_genre"]
    _PERIPH_ETAT[0], _PERIPH_ETAT[1] = signature, liste
    return liste


class Backend(QObject):
    disksChanged = pyqtSignal()
    networksChanged = pyqtSignal()
    messageRequested = pyqtSignal(str, str)
    sharesFound = pyqtSignal(list)
    sharesError = pyqtSignal(str)
    smartReady = pyqtSignal(str, str)
    netUpdated = pyqtSignal(list)
    printersUpdated = pyqtSignal(list)
    printersChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._disks = []
        self._networks = charger_cache_reseau()
        self._printers = charger_cache_imprimantes()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refreshDisks)
        self._timer.start(3000)
        self.netUpdated.connect(self._setNetworks)
        self.printersUpdated.connect(self._setPrinters)
        self._netWake = threading.Event()
        threading.Thread(target=lambda: sync_places(load_net_locations()), daemon=True).start()
        threading.Thread(target=self._netLoop, daemon=True).start()
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
        if new != self._disks:
            self._disks = new
            self.disksChanged.emit()

    def _netLoop(self):
        while True:
            try:
                liste = build_network_list()
                enregistrer_cache_reseau(liste)
                self.netUpdated.emit(liste)
                imprimantes = list_printers() + list_peripheriques()
                enregistrer_cache_imprimantes(imprimantes)
                self.printersUpdated.emit(imprimantes)
            except Exception as e:
                print("[ce_pc_plasma] réseau :", e)
            self._netWake.wait(3)
            self._netWake.clear()

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

    @pyqtSlot(str, result=str)
    def barIcon(self, name):
        # Icône de la barre de commandes : fichier du thème Fenestra s'il existe, sinon nom d'icône standard
        for base in (os.path.expanduser("~/.local/share/icons/windows-modern/places"),
                     "/usr/share/icons/windows-modern/places"):
            p = os.path.join(base, name + ".svg")
            if os.path.isfile(p):
                return "file://" + p
        return name

    @pyqtSlot(str)
    def saveBarMode(self, mode):
        if mode not in ("simple", "complet"):
            return
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = BAR_CONFIG + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"mode": mode}, f)
            os.replace(tmp, BAR_CONFIG)
        except OSError as e:
            print("[ce_pc_plasma] mode de la barre :", e)

    @pyqtSlot(int, int, bool)
    def saveWindow(self, w, h, maximized):
        # Mémorise la taille. Fenêtre agrandie : on garde la dernière taille normale déjà connue.
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
        # Seuls ces deux programmes peuvent être lancés depuis la barre
        if name not in ("systemsettings", "kinfocenter"):
            return
        try:
            subprocess.Popen([name])
        except OSError:
            self.messageRequested.emit(tr("Program not found"),
                                       tr("The program “{name}” is not installed on this computer.").format(name=name))

    @pyqtSlot()
    def refreshAll(self):
        self.refreshDisks()
        self._netWake.set()

    @pyqtSlot(str, str, str)
    def listShares(self, host, user, password):
        # Liste les partages d'un serveur avec smbclient (en arrière-plan). Le mot de passe passe par
        # l'environnement du processus, jamais par la ligne de commande, et n'est pas conservé.
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
        # Disque présent mais pas monté : on le monte, puis on l'ouvre dans Dolphin
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
        try:
            result = subprocess.run(
                ["udisksctl", "power-off", "-b", get_base_disk_device(device)],
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

    @pyqtSlot(str, result=str)
    def getLabel(self, device):
        try:
            return subprocess.check_output(
                ["lsblk", "-no", "LABEL", device],
                stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
        except Exception:
            return ""

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
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

QQC2.ApplicationWindow {
    id: win
    visible: true
    title: trad("This PC")
    // minRuban : le ruban complet a besoin d'environ 710 px de large
    minimumWidth: win.ribbon ? 740 : 640
    width: Math.max(barModeInit === "complet" ? 740 : 640, winInit.w > 0 ? winInit.w : 850)   // tailleInitiale
    // minHauteur : avec le ruban, il faut voir la première rangée de disques en entier
    minimumHeight: win.ribbon ? 520 : 540   // minHauteurSimple
    height: Math.max(barModeInit === "complet" ? 520 : 540, winInit.h > 0 ? winInit.h : 584)
    // fenetreMemo : la taille est enregistrée à la fermeture (visibility 4 = fenêtre agrandie)
    onClosing: backend.saveWindow(win.width, win.height, win.visibility === 4)
    Component.onCompleted: {
        if (winInit.max)
            win.showMaximized()
    }
    color: Kirigami.Theme.backgroundColor

    property bool foldersCollapsed: false
    property bool disksCollapsed: false
    property bool netCollapsed: false
    property bool printersCollapsed: false
    // seuilAlerte : au-delà de cette occupation (fraction du disque), les jauges passent au rouge
    readonly property real alertFrac: 0.8
    // couleursJauge : bleu des jauges normales et rouge des jauges en alerte (fixes)
    readonly property color gaugeBlue: "#1e88e5"
    readonly property color gaugeRed: "#d92b2b"
    // Disque ou emplacement réseau sélectionné par un clic (point de montage), vide si aucun
    property string selectedKey: ""

    property var propsDisk: ({})

    // grilleCartes : cartes de largeur fixe, autant de colonnes que la place le permet
    // (zone utile = largeur du défilement - 10 - marges 2 x 15 ; 8 d'espacement entre colonnes)
    readonly property real cardWidth: 340
    readonly property int cardCols: Math.max(1, Math.floor((scroll.availableWidth - 32) / (cardWidth + 8)))


    // Mode de la barre de commandes : false = simple (Windows 11), true = complet (ruban)
    property bool ribbon: barModeInit === "complet"

    // fenestraLangues : texte traduit (table envoyée par le programme), texte anglais si absent
    function trad(s) {
        var v = trTable[s]
        return v !== undefined ? v : s
    }

    // Disque ou emplacement réseau actuellement sélectionné (null si rien)
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
    // taille : Go avec une décimale sous 1 To, puis To avec trois chiffres significatifs
    function taille(n) {
        var g = n / 1073741824
        if (Math.round(g * 10) / 10 < 1024)
            return g.toFixed(1).replace(".", trDecimal) + " " + trad("GB")
        var t = g / 1024
        return t.toFixed(t < 10 ? 2 : (t < 100 ? 1 : 0)).replace(".", trDecimal) + " " + trad("TB")
    }
    function showPrinter(d) {
        printerDialog.info = d
        printerDialog.open()
    }
    function showProps(d) {
        propsDisk = d
        if (d.isUsb === true) {
            renameField.initial = d.rawLabel || ""
            renameField.text = renameField.initial
        }
        propsDialog.open()
    }

    QQC2.Dialog {
        id: propsDialog
        modal: true
        anchors.centerIn: QQC2.Overlay.overlay
        title: win.trad("Properties of {name}").replace("{name}", win.propsDisk.label || "")
        width: 460
        standardButtons: QQC2.Dialog.Close

        contentItem: ColumnLayout {
            spacing: 12

            GridLayout {
                columns: 2
                columnSpacing: 14
                rowSpacing: 6
                Layout.fillWidth: true

                QQC2.Label { text: win.trad("Type:"); opacity: 0.75 }
                QQC2.Label {
                    text: win.propsDisk.isNetwork ? win.trad("Network location") : (win.propsDisk.isOptical === true ? win.trad("CD/DVD Drive") : (win.propsDisk.isUsb ? win.trad("Removable disk") : win.trad("Local Disk")))
                    Layout.fillWidth: true
                }
                QQC2.Label { text: win.trad("File system:"); opacity: 0.75 }
                QQC2.Label { text: win.propsDisk.fstype || ""; Layout.fillWidth: true }
                QQC2.Label { text: win.trad("Mount point:"); opacity: 0.75 }
                QQC2.Label {
                    text: win.propsDisk.mountpoint || ""
                    Layout.fillWidth: true
                    elide: Text.ElideMiddle
                }
                QQC2.Label { text: win.trad("Device:"); opacity: 0.75 }
                QQC2.Label { text: win.propsDisk.device || ""; Layout.fillWidth: true }
            }

            Kirigami.Separator { Layout.fillWidth: true }

            RowLayout {
                spacing: 20
                Layout.alignment: Qt.AlignHCenter

                Canvas {
                    id: pie
                    Layout.preferredWidth: 100
                    Layout.preferredHeight: 100
                    property real frac: win.propsDisk.total > 0
                                        ? win.propsDisk.used / win.propsDisk.total : 0
                    property color usedColor: frac >= win.alertFrac ? win.gaugeRed
                                                                    : win.gaugeBlue
                    property color freeColor: Kirigami.Theme.alternateBackgroundColor
                    property color lineColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                                      Kirigami.Theme.textColor.g,
                                                      Kirigami.Theme.textColor.b, 0.35)
                    onFracChanged: requestPaint()
                    onUsedColorChanged: requestPaint()
                    onFreeColorChanged: requestPaint()
                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.reset()
                        var cx = width / 2
                        var cy = height / 2
                        var r = 46
                        ctx.lineWidth = 1
                        ctx.strokeStyle = lineColor
                        ctx.fillStyle = freeColor
                        ctx.beginPath()
                        ctx.arc(cx, cy, r, 0, 2 * Math.PI)
                        ctx.fill()
                        ctx.stroke()
                        if (frac > 0) {
                            ctx.fillStyle = usedColor
                            ctx.beginPath()
                            ctx.moveTo(cx, cy)
                            ctx.arc(cx, cy, r, -Math.PI / 2,
                                    -Math.PI / 2 + 2 * Math.PI * frac)
                            ctx.closePath()
                            ctx.fill()
                            ctx.stroke()
                        }
                    }
                }

                ColumnLayout {
                    spacing: 6
                    RowLayout {
                        spacing: 8
                        Rectangle { width: 10; height: 10; color: pie.usedColor }
                        QQC2.Label { text: win.trad("Used: {size}").replace("{size}", win.taille(win.propsDisk.used || 0)) }
                    }
                    RowLayout {
                        spacing: 8
                        Rectangle {
                            width: 10; height: 10
                            color: Kirigami.Theme.alternateBackgroundColor
                            border.width: 1
                            border.color: pie.lineColor
                        }
                        QQC2.Label { text: win.trad("Free: {size}").replace("{size}", win.taille(win.propsDisk.free || 0)) }
                    }
                    RowLayout {
                        spacing: 8
                        Rectangle {
                            width: 10; height: 10
                            color: Kirigami.Theme.backgroundColor
                            border.width: 1
                            border.color: pie.lineColor
                        }
                        QQC2.Label { text: win.trad("Capacity: {size}").replace("{size}", win.taille(win.propsDisk.total || 0)) }
                    }
                }
            }

            QQC2.Button {
                text: win.trad("SMART status…")
                visible: !win.propsDisk.isNetwork && win.propsDisk.isOptical !== true
                Layout.alignment: Qt.AlignHCenter
                onClicked: backend.showSmart(win.propsDisk.device)
            }

            RowLayout {
                visible: win.propsDisk.isUsb === true && win.propsDisk.mountpoint !== "/"
                         && !win.propsDisk.isNetwork
                Layout.fillWidth: true
                spacing: 8

                QQC2.TextField {
                    id: renameField
                    Layout.fillWidth: true
                    placeholderText: win.trad("Volume name")
                    property string initial: ""
                }
                QQC2.Button {
                    text: win.trad("Rename")
                    enabled: renameField.text.trim() !== "" && renameField.text !== renameField.initial
                    onClicked: {
                        backend.renameDevice(win.propsDisk.device, win.propsDisk.fstype,
                                             renameField.text)
                        propsDialog.close()
                    }
                }
            }
        }
    }

    QQC2.Dialog {
        id: smartDialog
        modal: true
        anchors.centerIn: QQC2.Overlay.overlay
        width: 640
        height: 460
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

    Connections {
        target: backend
        function onSmartReady(titre, texte) {
            smartDialog.title = titre
            smartText.text = texte
            smartDialog.open()
        }
        function onNetworksChanged() {
            if (!propsDialog.visible)
                return
            for (var k = 0; k < backend.networks.length; k++) {
                if (backend.networks[k].mountpoint === win.propsDisk.mountpoint) {
                    win.propsDisk = backend.networks[k]
                    break
                }
            }
        }
        function onDisksChanged() {
            if (!propsDialog.visible)
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
        QQC2.Label {
            id: msgLabel
            width: parent.width
            wrapMode: Text.Wrap
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
                    // Adresse collée : smb://utilisateur@serveur/partage
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
            QQC2.Label { text: win.trad("Shares found:"); opacity: 0.75; visible: addNetDialog.found.length > 0 }
            QQC2.ComboBox {
                id: netFound
                Layout.fillWidth: true
                visible: addNetDialog.found.length > 0
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

    // imprimantesInfo : fenêtre d'information sur une imprimante (lecture seule)
    QQC2.Dialog {
        id: printerDialog
        property var info: ({})
        modal: true
        anchors.centerIn: QQC2.Overlay.overlay
        title: info.label || ""
        width: 460
        standardButtons: QQC2.Dialog.Close

        contentItem: GridLayout {
            columns: 2
            columnSpacing: 12
            rowSpacing: 8

            QQC2.Label { text: win.trad("Type:"); opacity: 0.75 }
            QQC2.Label { text: win.trad("Printer"); Layout.fillWidth: true }
            QQC2.Label { text: win.trad("Status:"); opacity: 0.75 }
            QQC2.Label { text: printerDialog.info.statusText || ""; Layout.fillWidth: true }
            QQC2.Label { text: win.trad("Device:"); opacity: 0.75 }
            QQC2.Label {
                text: printerDialog.info.device || ""
                Layout.fillWidth: true
                wrapMode: Text.WrapAnywhere
            }
            QQC2.Button {
                visible: printerDialog.info.absent === true
                Layout.columnSpan: 2
                text: win.trad("Remove from list")
                onClicked: {
                    backend.forgetPrinter(printerDialog.info.name)
                    printerDialog.close()
                }
            }
            ColumnLayout {
                visible: (printerDialog.info.inks || []).length > 0
                Layout.columnSpan: 2
                Layout.fillWidth: true
                Layout.topMargin: 6
                spacing: 6

                QQC2.Label { text: win.trad("Ink levels"); opacity: 0.75 }
                Repeater {
                    model: printerDialog.info.inks || []
                    delegate: RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        QQC2.Label {
                            text: modelData.name
                            font: Kirigami.Theme.smallFont
                            elide: Text.ElideRight
                            Layout.preferredWidth: 150
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 10
                            color: Qt.tint(Kirigami.Theme.backgroundColor,
                                           Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                                   Kirigami.Theme.textColor.b, 0.16))
                            border.width: 1
                            border.color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                                  Kirigami.Theme.textColor.b, 0.38)
                            Rectangle {
                                x: 1; y: 1
                                width: (parent.width - 2) * modelData.level / 100
                                height: parent.height - 2
                                color: modelData.color
                            }
                        }
                        QQC2.Label {
                            text: modelData.level + " %"
                            font: Kirigami.Theme.smallFont
                            color: modelData.low ? win.gaugeRed : Kirigami.Theme.textColor
                            horizontalAlignment: Text.AlignRight
                            Layout.preferredWidth: 40
                        }
                    }
                }
            }
        }
    }

    // aProposDialog : fenêtre « À propos de Ce PC »
    QQC2.Dialog {
        id: aboutDialog
        modal: true
        anchors.centerIn: QQC2.Overlay.overlay
        title: win.trad("About This PC")
        width: 460
        standardButtons: QQC2.Dialog.Close

        contentItem: ColumnLayout {
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                spacing: 14
                Image {
                    visible: aboutInfo.icon !== ""
                    source: aboutInfo.icon
                    Layout.preferredWidth: 56
                    Layout.preferredHeight: 56
                    sourceSize.width: 112
                    sourceSize.height: 112
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    mipmap: true
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    QQC2.Label {
                        text: win.trad("This PC")
                        font.pixelSize: 22
                        font.bold: true
                    }
                    QQC2.Label {
                        text: win.trad("Version {version} · Plasma version").replace("{version}", aboutInfo.version)
                        opacity: 0.75
                    }
                }
            }

            Kirigami.Separator { Layout.fillWidth: true }

            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: win.trad("Part of the Fenestra series, which gives KDE Plasma the look of a Windows 11 desktop.")
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

            Kirigami.Separator { Layout.fillWidth: true }

            QQC2.Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                font: Kirigami.Theme.smallFont
                opacity: 0.65
                text: "Qt " + aboutInfo.qt + " · PyQt6 " + aboutInfo.pyqt + " · Python " + aboutInfo.python
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // barDeuxModes : barre de commandes, mode simple (Windows 11) ou complet (ruban)
        Item {
            id: barRoot
            // couleursBarre : couleurs « En-tête » du thème (comme la bande des menus de Dolphin)
            Kirigami.Theme.colorSet: Kirigami.Theme.Header
            Kirigami.Theme.inherit: false
            Layout.fillWidth: true
            Layout.preferredHeight: win.ribbon ? 118 : 44
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
            }
            function setRibbon(v) {
                if (win.ribbon === v)
                    return
                win.ribbon = v
                backend.saveBarMode(v ? "complet" : "simple")
                if (win.visibility !== 2)   // rubanAgrandie : fenêtre agrandie : on ne change pas sa taille
                    return
                if (v)
                    win.height = win.height + 74
                else
                    win.height = Math.max(win.minimumHeight, win.height - 74)
            }

            Rectangle {
                anchors.fill: parent
                color: Kirigami.Theme.backgroundColor
            }

            QQC2.Menu {
                id: addMenu
                QQC2.MenuItem {
                    text: win.trad("Network location…")
                    icon.name: "folder-network"
                    onTriggered: barRoot.runCmd("add")
                }
            }
            QQC2.Menu {
                id: moreMenu
                QQC2.MenuItem {
                    text: win.trad("Add a network location…")
                    icon.name: "folder-network"
                    onTriggered: barRoot.runCmd("add")
                }
                QQC2.MenuItem {
                    text: win.trad("System Settings")
                    icon.name: "preferences-system"
                    onTriggered: barRoot.runCmd("settings")
                }
                QQC2.MenuItem {
                    text: win.trad("System information")
                    icon.name: "computer"
                    onTriggered: barRoot.runCmd("info")
                }
                QQC2.MenuSeparator { }
                QQC2.MenuItem {
                    text: win.trad("About This PC")
                    icon.source: barIconsRuban["about"]
                    onTriggered: aboutDialog.open()
                }
            }

            // ===== MODE SIMPLE =====
            RowLayout {
                visible: !win.ribbon
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                spacing: 2

                // Bouton « + Ajouter ▾ »
                Item {
                    id: addBtn
                    Layout.preferredWidth: addRow.implicitWidth + 24
                    Layout.preferredHeight: 34
                    Layout.alignment: Qt.AlignVCenter

                    Rectangle {
                        anchors.fill: parent
                        radius: 5
                        visible: addMouse.containsMouse || addMouse.pressed || addMenu.visible
                        color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                       Kirigami.Theme.highlightColor.b, addMouse.pressed ? 0.28 : 0.14)
                        border.width: 1
                        border.color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                              Kirigami.Theme.highlightColor.b, 0.5)
                    }
                    Row {
                        id: addRow
                        anchors.centerIn: parent
                        spacing: 8
                        // iconesActions : « + » en icône du thème (repli : « + » en texte)
                        Item {
                            anchors.verticalCenter: parent.verticalCenter
                            width: 16
                            height: 16
                            readonly property string src: barIconsSimple["list-add"] || ""
                            readonly property bool isFile: src.indexOf("file://") === 0
                            Image {
                                anchors.fill: parent
                                visible: parent.isFile
                                source: parent.isFile ? parent.src : ""
                                sourceSize.width: 32
                                sourceSize.height: 32
                                fillMode: Image.PreserveAspectFit
                                smooth: true
                                mipmap: true
                            }
                            QQC2.Label {
                                anchors.centerIn: parent
                                visible: !parent.isFile
                                text: "+"
                                font.pixelSize: 20
                                font.bold: true
                                color: Kirigami.Theme.highlightColor
                            }
                        }
                        QQC2.Label {
                            anchors.verticalCenter: parent.verticalCenter
                            text: win.trad("Add")
                            color: Kirigami.Theme.textColor
                        }
                        Canvas {
                            anchors.verticalCenter: parent.verticalCenter
                            width: 12
                            height: 12
                            property color col: Kirigami.Theme.textColor
                            onColChanged: requestPaint()
                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.reset()
                                ctx.strokeStyle = col
                                ctx.lineWidth = 1.6
                                ctx.lineCap = "round"
                                ctx.lineJoin = "round"
                                ctx.beginPath()
                                ctx.moveTo(2, 4)
                                ctx.lineTo(6, 8)
                                ctx.lineTo(10, 4)
                                ctx.stroke()
                            }
                        }
                    }
                    MouseArea {
                        id: addMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: addMenu.popup(addBtn, 0, addBtn.height)
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 22
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    Layout.alignment: Qt.AlignVCenter
                    color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                   Kirigami.Theme.textColor.b, 0.25)
                }

                // Ouvrir / Propriétés / Actualiser : icônes seules, bulle au survol
                Repeater {
                    model: [
                        { t: win.trad("Open"), i: "folder-open", a: "open" },
                        { t: win.trad("Properties"), i: "document-properties", a: "props" },
                        { t: win.trad("Refresh"), i: "view-refresh", a: "refresh" }
                    ]
                    delegate: Item {
                        id: sBtn
                        readonly property bool usable: modelData.a === "open" ? barRoot.canOpen
                                                     : (modelData.a === "props" ? barRoot.canProps : true)
                        Layout.preferredWidth: 36
                        Layout.preferredHeight: 34
                        Layout.alignment: Qt.AlignVCenter
                        QQC2.ToolTip.visible: sMouse.containsMouse
                        QQC2.ToolTip.text: modelData.t
                        QQC2.ToolTip.delay: 500

                        Rectangle {
                            anchors.fill: parent
                            radius: 5
                            visible: sBtn.usable && (sMouse.containsMouse || sMouse.pressed)
                            color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                           Kirigami.Theme.highlightColor.b, sMouse.pressed ? 0.28 : 0.14)
                            border.width: 1
                            border.color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                                  Kirigami.Theme.highlightColor.b, 0.5)
                        }
                        Item {
                            anchors.centerIn: parent
                            width: 22
                            height: 22
                            opacity: sBtn.usable ? 1.0 : 0.45
                            readonly property string src: barIconsSimple[modelData.i] || modelData.i
                            readonly property bool isFile: src.indexOf("file://") === 0
                            Image {
                                anchors.fill: parent
                                visible: parent.isFile
                                source: parent.isFile ? parent.src : ""
                                sourceSize.width: 44
                                sourceSize.height: 44
                                fillMode: Image.PreserveAspectFit
                                smooth: true
                                mipmap: true
                            }
                            Kirigami.Icon {
                                anchors.fill: parent
                                visible: !parent.isFile
                                source: parent.isFile ? "" : parent.src
                            }
                        }
                        MouseArea {
                            id: sMouse
                            anchors.fill: parent
                            enabled: sBtn.usable
                            hoverEnabled: true
                            onClicked: barRoot.runCmd(modelData.a)
                        }
                    }
                }

                // Bouton « … »
                Item {
                    id: moreBtn
                    Layout.preferredWidth: 36
                    Layout.preferredHeight: 34
                    Layout.alignment: Qt.AlignVCenter
                    QQC2.ToolTip.visible: moreMouse.containsMouse
                    QQC2.ToolTip.text: win.trad("Show more")
                    QQC2.ToolTip.delay: 500
                    Rectangle {
                        anchors.fill: parent
                        radius: 5
                        visible: moreMouse.containsMouse || moreMouse.pressed || moreMenu.visible
                        color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                       Kirigami.Theme.highlightColor.b, moreMouse.pressed ? 0.28 : 0.14)
                        border.width: 1
                        border.color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                              Kirigami.Theme.highlightColor.b, 0.5)
                    }
                    QQC2.Label {
                        anchors.centerIn: parent
                        anchors.verticalCenterOffset: -3
                        text: "…"
                        font.pixelSize: 20
                        font.bold: true
                        color: Kirigami.Theme.textColor
                    }
                    MouseArea {
                        id: moreMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: moreMenu.popup(moreBtn, 0, moreBtn.height)
                    }
                }

                Item { Layout.fillWidth: true }

                // Flèche : ouvrir le mode complet
                Item {
                    id: expandBtn
                    Layout.preferredWidth: 32
                    Layout.preferredHeight: 32
                    Layout.alignment: Qt.AlignVCenter
                    QQC2.ToolTip.visible: expandMouse.containsMouse
                    QQC2.ToolTip.text: win.trad("Show the full ribbon")
                    QQC2.ToolTip.delay: 500
                    Rectangle {
                        anchors.fill: parent
                        radius: 5
                        visible: expandMouse.containsMouse || expandMouse.pressed
                        color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                       Kirigami.Theme.highlightColor.b, expandMouse.pressed ? 0.28 : 0.14)
                    }
                    Canvas {
                        anchors.centerIn: parent
                        width: 12
                        height: 12
                        property color col: Kirigami.Theme.textColor
                        onColChanged: requestPaint()
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.reset()
                            ctx.strokeStyle = col
                            ctx.lineWidth = 1.6
                            ctx.lineCap = "round"
                            ctx.lineJoin = "round"
                            ctx.beginPath()
                            ctx.moveTo(2, 4)
                            ctx.lineTo(6, 8)
                            ctx.lineTo(10, 4)
                            ctx.stroke()
                        }
                    }
                    MouseArea {
                        id: expandMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: barRoot.setRibbon(true)
                    }
                }
            }

            // Flèche : revenir au mode simple (rubanWindows : en haut à droite de la barre)
            Item {
                id: collapseBtn
                anchors.right: parent.right
                anchors.rightMargin: 8
                anchors.top: parent.top
                anchors.topMargin: 6
                z: 5
                visible: win.ribbon
                width: 32
                height: 26
                QQC2.ToolTip.visible: collapseMouse.containsMouse
                QQC2.ToolTip.text: win.trad("Collapse the ribbon")
                QQC2.ToolTip.delay: 500
                Rectangle {
                    anchors.fill: parent
                    radius: 5
                    visible: collapseMouse.containsMouse || collapseMouse.pressed
                    color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g,
                                   Kirigami.Theme.highlightColor.b, collapseMouse.pressed ? 0.28 : 0.14)
                }
                Canvas {
                    anchors.centerIn: parent
                    width: 12
                    height: 12
                    property color col: Kirigami.Theme.textColor
                    onColChanged: requestPaint()
                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.reset()
                        ctx.strokeStyle = col
                        ctx.lineWidth = 1.6
                        ctx.lineCap = "round"
                        ctx.lineJoin = "round"
                        ctx.beginPath()
                        ctx.moveTo(2, 8)
                        ctx.lineTo(6, 4)
                        ctx.lineTo(10, 8)
                        ctx.stroke()
                    }
                }
                MouseArea {
                    id: collapseMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: barRoot.setRibbon(false)
                }
            }

            // ===== MODE COMPLET (ruban) =====
            ColumnLayout {
                visible: win.ribbon
                anchors.fill: parent
                spacing: 0

                // Groupes du ruban
                RowLayout {
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

                    Item { Layout.fillWidth: true }
                }
            }
        }
        Kirigami.Separator { Layout.fillWidth: true }

        // Zone de contenu (couleurs "contenu" de Plasma)
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

                ColumnLayout {
                    x: 10
                    width: scroll.availableWidth - 10
                    spacing: 0

                    // En-tête de rubrique "Dossiers"
                    Item {
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
                        MouseArea {   // flecheSeule : seule la flèche replie la rubrique
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            width: 24
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.foldersCollapsed = !win.foldersCollapsed
                        }
                    }

                    // Grille des dossiers
                    GridLayout {
                        id: folderGrid
                        visible: !win.foldersCollapsed
                        Layout.fillWidth: true
                        Layout.leftMargin: 15
                        Layout.rightMargin: 15
                        Layout.bottomMargin: 8
                        readonly property real cellWidth: Math.max(216, frameWidth)
                        columns: Math.max(1, Math.floor((scroll.availableWidth - 32) / (cellWidth + 8)))
                        columnSpacing: 8
                        rowSpacing: 6
                        uniformCellWidths: true

                        // cadreDossiers : largeur du cadre = marge + icône + espace + nom le plus long + marge
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
                                // Cadre au survol (comme Dolphin) : couleur d'accent de Plasma, translucide
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
                                    onDoubleClicked: backend.openFolder(modelData.path)
                                }
                            }
                        }
                    }

                    // En-tête de rubrique "Périphériques et lecteurs"
                    Item {
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
                        MouseArea {   // flecheSeule : seule la flèche replie la rubrique
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            width: 24
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.disksCollapsed = !win.disksCollapsed
                        }
                    }

                    // Cartes des disques
                    GridLayout {
                        visible: !win.disksCollapsed
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

                                    Item {
                                        Layout.preferredWidth: 48
                                        Layout.preferredHeight: 48
                                        Layout.alignment: Qt.AlignVCenter
                                        Kirigami.Icon {
                                            anchors.centerIn: parent
                                            source: modelData.icon
                                            width: modelData.isKey ? 40 : 48
                                            height: width
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
                                            // jaugeGrise : fond de jauge gris clair (16 % de la couleur du texte sur le fond)
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
                                    }
                                }
                                // Fin cadre au survol ; au clic, fond teinté de la couleur d'accent (reste jusqu'à un autre clic)
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
                                    onDoubleClicked: function(mouse) {
                                        if (mouse.button === Qt.LeftButton)
                                            backend.openFolder(modelData.mountpoint)
                                    }
                                    onClicked: function(mouse) {
                                        if (mouse.button === Qt.RightButton)
                                            diskMenu.popup()
                                    }
                                }
                                QQC2.Menu {
                                    id: diskMenu
                                    QQC2.MenuItem {
                                        text: win.trad("Open with Dolphin")
                                        icon.name: "folder-open"
                                        onTriggered: backend.openFolder(modelData.mountpoint)
                                    }
                                    QQC2.MenuItem {
                                        text: win.trad("Safely remove")
                                        icon.name: "media-eject"
                                        visible: modelData.isUsb
                                        height: visible ? implicitHeight : 0
                                        onTriggered: backend.ejectDevice(modelData.device, modelData.label)
                                    }
                                    QQC2.MenuItem {
                                        text: win.trad("Eject")
                                        icon.name: "media-eject"
                                        visible: modelData.isOptical === true
                                        height: visible ? implicitHeight : 0
                                        onTriggered: backend.ejectOptical(modelData.device)
                                    }
                                    QQC2.MenuItem {
                                        text: win.trad("Properties")
                                        icon.name: "document-properties"
                                        onTriggered: win.showProps(modelData)
                                    }
                                }
                            }
                        }
                    }

                    // En-tête de rubrique "Emplacements réseau"
                    Item {
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
                        MouseArea {   // flecheSeule : seule la flèche replie la rubrique
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            width: 24
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.netCollapsed = !win.netCollapsed
                        }
                    }

                    // Cartes des emplacements réseau : partages montés et emplacements ajoutés par l'utilisateur
                    GridLayout {
                        visible: !win.netCollapsed
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
                                        // Pastille rouge : serveur non joignable
                                        // pastilleSvg : image vectorielle nette
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
                                            // jaugeGrise : fond de jauge gris clair (16 % de la couleur du texte sur le fond)
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

                                // Fin cadre au survol ; au clic, fond teinté de la couleur d'accent (reste jusqu'à un autre clic)
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
                                    onDoubleClicked: function(mouse) {
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

                    // Imprimantes et périphériques : information seulement, sous les emplacements réseau
                    Item {
                        visible: backend.printers.length > 0
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
                        MouseArea {   // flecheSeule : seule la flèche replie la rubrique
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            width: 24
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.printersCollapsed = !win.printersCollapsed
                        }
                    }

                    GridLayout {
                        visible: backend.printers.length > 0 && !win.printersCollapsed
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
                                    onDoubleClicked: function(mouse) {
                                        if (mouse.button === Qt.LeftButton && !modelData.indicator)
                                            win.showPrinter(modelData)
                                    }
                                    onClicked: function(mouse) {
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

            // clicVideSelection : un clic à côté d'un disque retire sa sélection.
            // Zone transparente au-dessus du contenu ; elle n'accepte pas le clic, qui continue donc
            // vers l'élément dessous (qui se resélectionne s'il est visé).
            MouseArea {
                z: 10
                anchors.fill: parent
                onPressed: function(mouse) {
                    win.selectedKey = ""
                    mouse.accepted = false
                }
            }
        }

        // Pied de page
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
                text: win.trad("{n} item(s)").replace("{n}", (win.foldersCollapsed ? 0 : foldersModel.length) + (win.disksCollapsed ? 0 : backend.disks.length) + (win.netCollapsed ? 0 : backend.networks.length) + (win.printersCollapsed ? 0 : backend.printers.length))
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


def bar_icons_actions():
    """Icônes du mode simple de la barre : windows-modern/actions/ d'abord, sinon l'icône habituelle de la barre."""
    res = bar_icons()
    for name in ("folder-open", "document-properties", "view-refresh", "list-add"):
        if name not in res:
            res[name] = ""
        for base in (os.path.expanduser("~/.local/share/fenestra/ce-pc-icones"),
                     os.path.expanduser("~/.local/share/icons/windows-modern/actions"),
                     "/usr/share/icons/windows-modern/actions"):
            p = os.path.join(base, name + ".svg")
            if os.path.isfile(p):
                res[name] = "file://" + p
                break
    return res


APP_VERSION = '1.0'
APP_AUTEUR = 'naykkalak'
APP_LICENCE = 'GPL-3.0-or-later'


def about_info():
    """Informations affichées par « À propos de Ce PC »."""
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    icone = os.path.join(ICON_CACHE_DIR, "app-icon.png")
    return {"version": APP_VERSION, "auteur": APP_AUTEUR, "licence": {"GPL-3.0-or-later": tr("GPL-3.0 or later"), "GPL-3.0-only": tr("GPL-3.0 only")}.get(APP_LICENCE, APP_LICENCE),   # licenceTraduite
            "qt": QT_VERSION_STR, "pyqt": PYQT_VERSION_STR,
            "python": "%d.%d.%d" % sys.version_info[:3],
            "icon": ("file://" + icone) if os.path.isfile(icone) else ""}


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
              ("about", "apps/help-about", "help-about"))
    res = {"refresh": actualiser or "view-refresh"}
    for cle, sous, repli in voulus:
        res[cle] = repli
        for base in (os.path.expanduser("~/.local/share/icons/windows-modern"),
                     "/usr/share/icons/windows-modern"):
            p = os.path.join(base, sous + ".svg")
            if os.path.isfile(p):
                res[cle] = "file://" + p
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
    theme = get_icon_theme_name()
    QIcon.setThemeName(theme)

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
    engine.rootContext().setContextProperty("aboutInfo", about_info())
    engine.rootContext().setContextProperty("barIconsSimple", bar_icons_actions())
    engine.rootContext().setContextProperty("barModeInit", load_bar_mode())
    engine.rootContext().setContextProperty("winInit", load_window_state())
    engine.rootContext().setContextProperty("badgeOffline", badge_offline())
    engine.loadData(QML.encode("utf-8"))
    if not engine.rootObjects():
        print("Échec du chargement QML")
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
