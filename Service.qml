import QtQuick
import Quickshell.Io

// Headless. Runs scheduler.py tick every 30 seconds; the script switches the
// theme only when the light or dark period has changed since the last switch,
// so a manual theme change sticks until the next scheduled switch.
Item {
  id: root
  visible: false

  readonly property string scriptPath: decodeURIComponent(Qt.resolvedUrl("scheduler.py").toString().replace("file://", ""))

  Timer {
    interval: 30000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: if (!tickProcess.running) tickProcess.running = true
  }

  Process {
    id: tickProcess
    command: ["python3", root.scriptPath, "tick"]

    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("theme-scheduler", text.trim())
    }
  }
}
