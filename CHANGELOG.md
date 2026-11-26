# Changelog

## 2.1.1

### Fixed
- When Discover is not installed (for example on Manjaro), the new-version check and its banner stay active, so you are still told when an update is available. The weekly-check option in Options is shown in that case.

### Distributions
- Tested on Fedora, Kubuntu, openSUSE, Manjaro and Solus.

## 2.1

### New
- **Plasma component**: This PC is now a Plasma component (`org.fenestra.cepc`) that you download from the KDE Store, with the program, its icons and its languages inside it. Run `bash install.sh` once: it looks for an older version (1.x or 2.0) and asks before uninstalling it (your settings are kept), checks the requirements, adds a "This PC" entry to the application menu and puts a "This PC" shortcut on the desktop.
- **Updates through Discover**: from version 2.1, updates are offered by Discover, with your other updates.
- **IP address** of a network printer, in its window (single or double click on the printer).
- **Compact toolbar** (Options): the compact bar is back as an option. It has the same commands as the ribbon, shown as icons only (Fluent UI System Icons, MIT licence, recoloured by the theme) with a tooltip on each. Adding a network location and choosing which sections to show are in the "…" menu. The full ribbon stays the default.
- **Disk properties in their own window**, with three tabs:
  - **General**: brand, model, firmware, serial number (hidden by default), usage ring, Open in Dolphin, Analyze space (Filelight).
  - **Health**: state, remaining lifetime (the bar starts full and empties with wear), spare, errors, power-on time, power cycles, unexpected shutdowns, data written and read, temperature; quick and full self-tests with progress bar.
  - **Surface**: read-only scan of every sector, 1,000-square map, slow and damaged sectors counted.
- **Disks in a USB enclosure**: health and self-tests through the enclosure (`smartctl`, administrator password).
- **Format…** in the right-click menu: exFAT, FAT32, NTFS, ext4, quick or full format, progress bar, Cancel during a full format. System volumes are protected.
- **Padlock** on encrypted (LUKS) volumes: an open padlock (grey body, blue bands, drawn for Fenestra) over the drive icon: a locked container is not shown in This PC, so only the unlocked state exists. Removable drives use the drive icon with a USB mark instead of a flash-drive icon.
- **Encrypted USB drives** are now recognised as removable: "Safely remove" appears in the right-click menu (the volume is unmounted, the container locked, then the drive powered off), and they are listed after the optical drive like other removable disks.
- **Ink levels on the printer card**: one small vertical bar per cartridge, in the ink's colour; a single or double click on the printer opens its window with the exact percentages. Below the printer's low-level mark, a fixed red line under the status names the colours ("Low ink level: Black"). The threshold is the one the printer declares (marker-low-levels); if it declares none, 10 % is used. Hidden while the printer is offline.
- **Version of This PC** shown in "About".
- **The install script offers to install the missing system tools** (smartmontools, exfatprogs, ntfs-3g…) with your distribution's package manager, after asking. Nothing is installed without your agreement, and applications stay your choice.
- New application icon.
- **Options window** (System group of the ribbon):
  - **Open items with a single click**: linked both ways to the Plasma setting, so it also applies to Dolphin and the desktop (suggested by Sadi58).
  - **Free space alert** adjustable from 20 % to 10 % (20 % by default): the drive gauge turns red below this level. The slider reads in the same direction as the gauge.
  - **Check for a new version once a week** (only shown when updates do not go through Discover).
- **Missing Skanpage**: when the scanner card cannot open Skanpage because it is not installed, the message now offers **Search in Discover** (only if Discover is present). Nothing is installed by This PC.
- **Scroll bar in the Windows 11 style**, the same as in Fenestra Menu 11: hidden at rest, thin while the mouse moves, wider on hover.

### Fixed
- A Wi-Fi printer or scanner no longer turns grey for a few seconds after a short network drop.
- "Add a network location": the "Shares found" line is always shown (greyed out until "List" finds shares), so the window no longer changes size.
- Right-click menu of a disk closing before the click (the disk list refreshes every 3 seconds).
- A network scanner seen by two drivers (escl and epson2) was shown twice, once as "USB".
- `/boot` is no longer listed as a drive.
- PyQt6 detection in the install script.
- Crash when closing the window while background searches were running.

### Languages
- **Portuguese (Portugal)** added, for 14 languages in total: English, French, German, Spanish, Italian, Portuguese (Portugal), Portuguese (Brazil), Dutch, Polish, Russian, Turkish, Japanese, Simplified Chinese and Traditional Chinese.
- All new texts translated in the 14 languages.
