/*
 * Fenestra This PC — part of the Fenestra series
 * https://github.com/naykkalak/fenestra-ce-pc
 * SPDX-FileCopyrightText: naykkalak (2026) for Fenestra This PC
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.extras as PlasmaExtras
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    Plasmoid.icon: "computer"
    toolTipMainText: i18n("This PC")
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground   // only the icon, no grey frame on the desktop

    function path(name) { return String(Qt.resolvedUrl(name)).replace("file://", "") }

    // Opens the window: the Python program is stored in this component, so a Discover update updates it.
    // setsid -f returns at once; a second click only brings the open window to the front.
    function openWindow() {
        executable.exec("setsid -f python3 '" + path("../code/ce_pc_plasma.py") + "'")
    }

    // Menu entry and desktop shortcut pointing to the program of this component
    Component.onCompleted: executable.exec("sh '" + path("../code/shortcut.sh") + "'")

    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => disconnectSource(source)
        function exec(cmd) { if (cmd) connectSource(cmd) }
    }

    // Panel: a plain icon, opened with one click like any panel icon.
    component PanelIcon: MouseArea {
        Layout.minimumWidth: Kirigami.Units.iconSizes.small
        Layout.minimumHeight: Kirigami.Units.iconSizes.small
        hoverEnabled: true
        onClicked: root.openWindow()
        Kirigami.Icon {
            anchors.fill: parent
            source: "computer"
            active: parent.containsMouse
        }
    }

    // Desktop: looks like a desktop icon (icon + name below) and follows the system setting:
    // one click if the user chose single-click, otherwise a double click.
    component DesktopIcon: MouseArea {
        readonly property bool singleClick: Qt.styleHints.singleClickActivation
        implicitWidth: Math.max(Kirigami.Units.iconSizes.large, label.implicitWidth) + Kirigami.Units.smallSpacing * 2
        implicitHeight: Kirigami.Units.iconSizes.large + Kirigami.Units.smallSpacing + label.implicitHeight + Kirigami.Units.smallSpacing * 2
        Layout.minimumWidth: implicitWidth
        Layout.minimumHeight: implicitHeight
        Layout.preferredWidth: implicitWidth
        Layout.preferredHeight: implicitHeight
        hoverEnabled: true
        onClicked: if (singleClick) root.openWindow()
        onDoubleClicked: if (!singleClick) root.openWindow()

        PlasmaExtras.Highlight {   // hover frame taken from the Plasma theme, like the other desktop icons
            // Plasma snaps desktop items to its grid, so the frame fits the icon and name, not the item
            anchors.centerIn: parent
            width: Math.max(content.width + Kirigami.Units.largeSpacing * 2, Kirigami.Units.iconSizes.huge + Kirigami.Units.gridUnit)
            height: content.height + Kirigami.Units.smallSpacing * 2
            hovered: true
            opacity: parent.containsMouse ? 1 : 0
        }
        Column {
            id: content
            anchors.centerIn: parent
            spacing: Kirigami.Units.smallSpacing
            Kirigami.Icon {
                anchors.horizontalCenter: parent.horizontalCenter
                width: Kirigami.Units.iconSizes.large
                height: width
                source: "computer"
            }
            PlasmaExtras.ShadowedLabel {   // the label Plasma itself uses for desktop icons
                id: label
                anchors.horizontalCenter: parent.horizontalCenter
                text: i18n("This PC")
            }
        }
    }

    // On the desktop Plasma shows the full representation, in a panel the compact one.
    compactRepresentation: PanelIcon {}
    fullRepresentation: DesktopIcon {}
}
