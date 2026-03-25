import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    property var lmonitaState: null

    Timer {
        interval: 1000
        running: true
        repeat: true
        onTriggered: {
            var req = new XMLHttpRequest();
            req.open("GET", "http://127.0.0.1:12344/state");
            req.onreadystatechange = function() {
                if (req.readyState === XMLHttpRequest.DONE) {
                    if (req.status === 200) {
                        try {
                            root.lmonitaState = JSON.parse(req.responseText);
                        } catch (e) {
                            console.error("LMonita parse error:", e);
                        }
                    } else {
                        root.lmonitaState = null;
                    }
                }
            }
            req.send();
        }
    }

    compactRepresentation: PlasmaComponents.Label {
        text: root.lmonitaState && root.lmonitaState.connected 
              ? (root.lmonitaState.server_status === "generating" ? "Generating..." : "LM Studio")
              : "LMonita (Offline)"
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }

    fullRepresentation: Item {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 18
        Layout.minimumHeight: Kirigami.Units.gridUnit * 18

        ColumnLayout {
            anchors.fill: parent
            spacing: Kirigami.Units.smallSpacing
            
            // Header
            RowLayout {
                Layout.fillWidth: true
                
                Rectangle {
                    width: Kirigami.Units.largeSpacing
                    height: width
                    radius: width / 2
                    color: !root.lmonitaState || !root.lmonitaState.connected ? Kirigami.Theme.negativeTextColor :
                           (root.lmonitaState.server_status === "generating" ? Kirigami.Theme.neutralTextColor : Kirigami.Theme.positiveTextColor)
                }
                
                PlasmaComponents.Label {
                    text: "LM STUDIO"
                    font.weight: Font.Bold
                    Layout.fillWidth: true
                }
                
                PlasmaComponents.Label {
                    text: root.lmonitaState && root.lmonitaState.connected ? "CONNECTED" : "OFFLINE"
                    color: !root.lmonitaState || !root.lmonitaState.connected ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.positiveTextColor
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                }
            }
            
            Kirigami.Separator { Layout.fillWidth: true }
            
            // Model Info
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0
                
                PlasmaComponents.Label {
                    text: root.lmonitaState && root.lmonitaState.model ? root.lmonitaState.model.name : "No model loaded"
                    font.weight: Font.Bold
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                
                PlasmaComponents.Label {
                    text: {
                        if (!root.lmonitaState || !root.lmonitaState.model) return "";
                        let m = root.lmonitaState.model;
                        let parts = [];
                        if (m.arch) parts.push(m.arch);
                        if (m.quant) parts.push(m.quant);
                        if (m.ctx) parts.push(m.ctx + " ctx");
                        return parts.join(" · ");
                    }
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    color: Kirigami.Theme.disabledTextColor
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
            
            Kirigami.Separator { Layout.fillWidth: true }

            // Last Generation
            PlasmaComponents.Label {
                text: "LAST GENERATION"
                font.pointSize: Kirigami.Theme.smallFont.pointSize
                font.weight: Font.Bold
                color: Kirigami.Theme.disabledTextColor
            }
            
            RowLayout {
                Layout.fillWidth: true
                
                PlasmaComponents.Label {
                    text: root.lmonitaState && root.lmonitaState.last_gen ? root.lmonitaState.last_gen.tps.toFixed(1) : "—"
                    font.pointSize: Kirigami.Theme.defaultFont.pointSize * 2
                    font.weight: Font.Bold
                    color: Kirigami.Theme.highlightColor
                }
                
                PlasmaComponents.Label {
                    text: "t/s"
                    Layout.alignment: Qt.AlignBottom
                    color: Kirigami.Theme.disabledTextColor
                }
            }
            
            GridLayout {
                columns: 3
                Layout.fillWidth: true
                columnSpacing: Kirigami.Units.largeSpacing
                
                // Row 1
                ColumnLayout {
                    spacing: 0
                    PlasmaComponents.Label { text: "TTFT"; font.pointSize: Kirigami.Theme.smallFont.pointSize; color: Kirigami.Theme.disabledTextColor }
                    PlasmaComponents.Label { text: root.lmonitaState && root.lmonitaState.last_gen ? (root.lmonitaState.last_gen.ttft_sec * 1000).toFixed(0) + "ms" : "—" }
                }
                ColumnLayout {
                    spacing: 0
                    PlasmaComponents.Label { text: "TIME"; font.pointSize: Kirigami.Theme.smallFont.pointSize; color: Kirigami.Theme.disabledTextColor }
                    PlasmaComponents.Label { text: root.lmonitaState && root.lmonitaState.last_gen ? root.lmonitaState.last_gen.total_sec.toFixed(1) + "s" : "—" }
                }
                ColumnLayout {
                    spacing: 0
                    PlasmaComponents.Label { text: "TOKENS"; font.pointSize: Kirigami.Theme.smallFont.pointSize; color: Kirigami.Theme.disabledTextColor }
                    PlasmaComponents.Label { text: root.lmonitaState && root.lmonitaState.last_gen ? root.lmonitaState.last_gen.total_tokens : "—" }
                }
                
                // Row 2
                ColumnLayout {
                    spacing: 0
                    PlasmaComponents.Label { text: "PROMPT"; font.pointSize: Kirigami.Theme.smallFont.pointSize; color: Kirigami.Theme.disabledTextColor }
                    PlasmaComponents.Label { text: root.lmonitaState && root.lmonitaState.last_gen ? root.lmonitaState.last_gen.prompt_tokens : "—" }
                }
                ColumnLayout {
                    spacing: 0
                    PlasmaComponents.Label { text: "GEN'D"; font.pointSize: Kirigami.Theme.smallFont.pointSize; color: Kirigami.Theme.disabledTextColor }
                    PlasmaComponents.Label { text: root.lmonitaState && root.lmonitaState.last_gen ? root.lmonitaState.last_gen.predicted_tokens : "—" }
                }
                ColumnLayout {
                    spacing: 0
                    PlasmaComponents.Label { text: "STOP"; font.pointSize: Kirigami.Theme.smallFont.pointSize; color: Kirigami.Theme.disabledTextColor }
                    PlasmaComponents.Label { text: root.lmonitaState && root.lmonitaState.last_gen ? (root.lmonitaState.last_gen.stop_reason || "—") : "—"; elide: Text.ElideRight; Layout.maximumWidth: Kirigami.Units.gridUnit * 4 }
                }
            }

            Kirigami.Separator { Layout.fillWidth: true }
            
            // Session
            PlasmaComponents.Label {
                text: "SESSION"
                font.pointSize: Kirigami.Theme.smallFont.pointSize
                font.weight: Font.Bold
                color: Kirigami.Theme.disabledTextColor
            }
            
            RowLayout {
                Layout.fillWidth: true
                
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    PlasmaComponents.Label { text: "GENS"; font.pointSize: Kirigami.Theme.smallFont.pointSize; color: Kirigami.Theme.disabledTextColor }
                    PlasmaComponents.Label { text: root.lmonitaState && root.lmonitaState.model_totals ? root.lmonitaState.model_totals.gens : "0"; font.weight: Font.Bold }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    PlasmaComponents.Label { text: "TOKENS"; font.pointSize: Kirigami.Theme.smallFont.pointSize; color: Kirigami.Theme.disabledTextColor }
                    PlasmaComponents.Label { text: root.lmonitaState && root.lmonitaState.model_totals ? root.lmonitaState.model_totals.tokens : "0"; font.weight: Font.Bold; color: Kirigami.Theme.highlightColor }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    PlasmaComponents.Label { text: "QUEUE"; font.pointSize: Kirigami.Theme.smallFont.pointSize; color: Kirigami.Theme.disabledTextColor }
                    PlasmaComponents.Label { text: root.lmonitaState ? root.lmonitaState.queued_requests : "0"; font.weight: Font.Bold; color: (root.lmonitaState && root.lmonitaState.queued_requests > 0) ? Kirigami.Theme.neutralTextColor : Kirigami.Theme.textColor }
                }
            }
            
            Item { Layout.fillHeight: true } // Spacer
            
            // Footer
            RowLayout {
                Layout.fillWidth: true
                PlasmaComponents.Label {
                    text: "http://localhost:1234"
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    color: Kirigami.Theme.disabledTextColor
                    Layout.fillWidth: true
                }
                PlasmaComponents.Label {
                    text: root.lmonitaState ? (root.lmonitaState.api_latency_ms + "ms  stream " + (root.lmonitaState.log_stream_active ? "✓" : "…")) : "offline"
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    color: Kirigami.Theme.disabledTextColor
                }
            }
        }
    }
}
