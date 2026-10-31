import QtQuick
import QtQuick.Shapes
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// The picker: which themes to use, how the day is split, and where the sun is.
// Every control saves straight to scheduler.py. Nothing changes the theme until
// the next switch or until "Apply now" is pressed.
Panel {
  id: root
  moduleName: "johnhaining.kyber-shift"
  ipcTarget: "johnhaining.kyber-shift"

  property Item anchorItem: null

  readonly property string scriptPath: decodeURIComponent(Qt.resolvedUrl("scheduler.py").toString().replace("file://", ""))

  property var status: null
  property var config: ({ mode: "fixed", schedule: "sun" })
  property var themes: []
  property bool savePending: false

  readonly property string periodKind: status && status.period ? status.period.kind : ""
  readonly property bool configured: status ? status.configured === true : false

  function refresh() {
    if (!statusProc.running) statusProc.running = true
  }

  function applyStatus(raw) {
    var data = null
    try {
      data = JSON.parse(String(raw || ""))
    } catch (e) {
      console.warn("kyber-shift", "Could not read status", e)
      return
    }
    if (!data || typeof data !== "object") return
    status = data
    config = data.config
    themes = data.themes || []
  }

  function themeName(slug) {
    for (var i = 0; i < themes.length; i++) {
      if (themes[i].slug === slug) return themes[i].name
    }
    return slug ? slug : "no theme chosen"
  }

  function themeOptions(mode) {
    return themes.filter(function(t) { return t.mode === mode })
      .map(function(t) { return { value: t.slug, label: t.name } })
  }

  function periodLabel(kind) {
    return kind === "light" ? "Light" : "Dark"
  }

  // Sunrise and sunset when the schedule follows the sun, otherwise the two clock times.
  function transitionName(kind) {
    if (config.schedule === "sun") return kind === "light" ? "Sunrise" : "Sunset"
    return kind === "light" ? "Light" : "Dark"
  }

  function clockText(iso) {
    return Qt.formatTime(new Date(iso), "HH:mm")
  }

  // "2h 14m" or "9m" until the given instant. Reads clockNow so it re-evaluates each tick.
  function countdownText(iso) {
    var minutes = Math.max(0, Math.ceil((new Date(iso).getTime() - clockNow) / 60000))
    var hours = Math.floor(minutes / 60)
    var rest = minutes % 60
    return hours > 0 ? hours + "h " + rest + "m" : rest + "m"
  }

  property real clockNow: Date.now()

  // Where we are between the start and end of the scheduled period, 0 to 1.
  readonly property real arcProgress: {
    if (!configured || !status.period || !status.next) return 0
    var from = new Date(status.period.since).getTime()
    var to = new Date(status.next.at).getTime()
    return Math.min(1, Math.max(0, (clockNow - from) / (to - from)))
  }

  // Arc end labels: the period start on the left and its end on the right.
  function edgeLabel(left) {
    if (!configured || !status.period || !status.next) return ""
    var nextKind = status.next.kind
    if (left) return transitionName(nextKind === "light" ? "dark" : "light") + "\n" + clockText(status.period.since)
    return transitionName(nextKind) + "\n" + clockText(status.next.at)
  }

  Timer {
    id: countdownTick
    interval: 15000
    running: true
    repeat: true
    onTriggered: {
      root.clockNow = Date.now()
      // Past the switch: pull fresh status so the next transition is shown.
      if (root.status && root.status.next && new Date(root.status.next.at).getTime() <= root.clockNow) root.refresh()
    }
  }

  function saveSettings(patch) {
    config = Object.assign({}, config, patch)
    if (saveProc.running) {
      savePending = true
      return
    }
    runSave()
  }

  function runSave() {
    savePending = false
    saveProc.command = ["python3", root.scriptPath, "save", JSON.stringify(config)]
    saveProc.running = true
  }

  // Flip the theme on screen to the other period's. It holds until the next switch.
  function toggleLightDark() {
    if (toggleProc.running) return
    toggleProc.running = true
  }

  function applyNow() {
    if (applyProc.running) return
    applyProc.command = ["python3", root.scriptPath, "apply", JSON.stringify(config)]
    applyProc.running = true
  }

  // Parse a text field into a coordinate, or null when it is blank or not a number.
  function numberOrNull(text) {
    var trimmed = String(text).trim()
    if (trimmed === "") return null
    var value = Number(trimmed)
    return isNaN(value) ? null : value
  }

  // Always on: the bar icon reads the period from here, so it must flip at
  // sunset even when the picker is closed.
  Timer {
    interval: 30000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Process {
    id: statusProc
    command: ["python3", root.scriptPath, "status"]

    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyStatus(text)
    }
  }

  Process {
    id: saveProc

    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("kyber-shift", text.trim())
    }

    onExited: {
      if (root.savePending) root.runSave()
      else root.refresh()
    }
  }

  Process {
    id: toggleProc
    command: ["python3", root.scriptPath, "toggle"]

    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("kyber-shift", text.trim())
    }

    onExited: root.refresh()
  }

  Process {
    id: applyProc

    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("kyber-shift", text.trim())
    }

    onExited: root.refresh()
  }

  onOpenedChanged: if (opened) refresh()

  PopupCard {
    id: card
    anchorItem: root.anchorItem
    owner: root
    bar: root.bar
    open: root.opened
    contentWidth: card.fittedContentWidth(Style.space(320))
    contentHeight: card.fittedContentHeight(column.implicitHeight)

    Column {
      id: column
      anchors.fill: parent
      spacing: Style.space(8)

      Text {
        text: "Kyber Shift"
        color: Commons.Color.popups.text
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        font.bold: true
      }

      Text {
        visible: root.status !== null
        width: parent.width
        wrapMode: Text.WordWrap
        color: Commons.Color.popups.text
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
        text: !root.configured
          ? "Not set up yet. Pick a light and a dark theme to start."
          : root.periodLabel(root.periodKind) + " theme now: " + root.themeName(root.status.scheduled)
      }

      Text {
        visible: root.configured && root.status.next !== null
        width: parent.width
        wrapMode: Text.WordWrap
        color: Qt.darker(Commons.Color.popups.text, 1.4)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        text: root.configured && root.status.next
          ? "Switches to " + root.periodLabel(root.status.next.kind).toLowerCase() + " at " + root.clockText(root.status.next.at)
          : ""
      }

      // Day runs from sunrise on the left to sunset on the right. Night runs the
      // other way, from sunset to sunrise, so the moon always travels toward morning.
      Item {
        id: sky
        width: parent.width
        height: Style.space(90)
        visible: root.configured && root.status.period !== undefined

        readonly property real margin: Style.space(34)
        readonly property real arcHeight: Style.space(42)
        readonly property real horizon: height - Style.space(24)
        readonly property real glyphX: margin + root.arcProgress * (width - 2 * margin)
        readonly property real glyphY: horizon - arcHeight * Math.sin(Math.PI * root.arcProgress)

        Shape {
          anchors.fill: parent
          preferredRendererType: Shape.CurveRenderer

          ShapePath {
            strokeColor: Qt.darker(Commons.Color.popups.text, 1.6)
            strokeWidth: 1
            fillColor: "transparent"
            strokeStyle: ShapePath.DashLine
            dashPattern: [4, 4]
            startX: sky.margin
            startY: sky.horizon

            PathArc {
              x: sky.width - sky.margin
              y: sky.horizon
              radiusX: (sky.width - 2 * sky.margin) / 2
              radiusY: sky.arcHeight
              direction: PathArc.Counterclockwise
            }
          }
        }

        Text {
          id: clockLabel
          x: sky.glyphX - width / 2
          y: sky.glyphY - height - Style.space(2)
          text: Qt.formatTime(new Date(root.clockNow), "HH:mm")
          color: Commons.Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          font.bold: true
          Behavior on x { NumberAnimation { duration: 900; easing.type: Easing.InOutQuad } }
          Behavior on y { NumberAnimation { duration: 900; easing.type: Easing.InOutQuad } }
        }

        Text {
          id: glyph
          x: sky.glyphX - width / 2
          y: sky.glyphY - height / 2
          text: root.periodKind === "dark" ? "\uf186" : "\uf185"
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.font.title
          color: Commons.Color.accent
          Behavior on x { NumberAnimation { duration: 900; easing.type: Easing.InOutQuad } }
          Behavior on y { NumberAnimation { duration: 900; easing.type: Easing.InOutQuad } }
        }

        Text {
          x: sky.margin - width / 2
          y: sky.horizon + Style.space(4)
          horizontalAlignment: Text.AlignHCenter
          text: root.edgeLabel(true)
          color: Qt.darker(Commons.Color.popups.text, 1.4)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }

        Text {
          x: sky.width - sky.margin - width / 2
          y: sky.horizon + Style.space(4)
          horizontalAlignment: Text.AlignHCenter
          text: root.edgeLabel(false)
          color: Qt.darker(Commons.Color.popups.text, 1.4)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }

      Text {
        visible: root.configured && root.status.next !== null
        width: parent.width
        font.family: Style.font.family
        font.pixelSize: Style.font.subtitle
        font.bold: true
        color: Commons.Color.accent
        text: root.configured && root.status.next
          ? root.transitionName(root.status.next.kind) + " in " + root.countdownText(root.status.next.at)
          : ""
      }

      Text {
        visible: root.status !== null && root.status.error !== null && root.status.error !== undefined
        width: parent.width
        wrapMode: Text.WordWrap
        color: Commons.Color.urgent
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        text: root.status && root.status.error ? "Last switch failed: " + root.status.error : ""
      }

      PanelSectionHeader {
        text: "Mode"
      }

      Dropdown {
        width: parent.width
        label: "Themes"
        value: root.config.mode || "fixed"
        options: [
          { value: "fixed", label: "Fixed light and dark" },
          { value: "random", label: "Random each day" }
        ]
        onChanged: function(value) { root.saveSettings({ mode: value }) }
      }

      Dropdown {
        width: parent.width
        label: "Switch at"
        value: root.config.schedule || "sun"
        options: [
          { value: "sun", label: "Sunrise and sunset" },
          { value: "clock", label: "Fixed times" }
        ]
        onChanged: function(value) { root.saveSettings({ schedule: value }) }
      }

      // Sunrise and sunset need a location. The weather widget's saved location
      // is used unless latitude and longitude are set here.
      Column {
        width: parent.width
        spacing: Style.space(6)
        visible: root.config.schedule === "sun"

        Text {
          width: parent.width
          wrapMode: Text.WordWrap
          color: Qt.darker(Commons.Color.popups.text, 1.4)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          text: root.status && root.status.location && root.status.location.latitude !== null
            ? "Location from " + (root.status.location.source === "weather" ? "the weather widget" : "these settings")
              + ": " + root.status.location.latitude.toFixed(2) + ", " + root.status.location.longitude.toFixed(2)
            : "No location yet. Set latitude and longitude, or set the weather location."
        }

        Row {
          width: parent.width
          spacing: Style.space(8)

          TextField {
            width: (parent.width - Style.space(8)) / 2
            placeholderText: "Latitude"
            text: root.config.latitude !== null && root.config.latitude !== undefined ? String(root.config.latitude) : ""
            onEditingFinished: root.saveSettings({ latitude: root.numberOrNull(text) })
          }

          TextField {
            width: (parent.width - Style.space(8)) / 2
            placeholderText: "Longitude"
            text: root.config.longitude !== null && root.config.longitude !== undefined ? String(root.config.longitude) : ""
            onEditingFinished: root.saveSettings({ longitude: root.numberOrNull(text) })
          }
        }
      }

      Row {
        width: parent.width
        spacing: Style.space(8)
        visible: root.config.schedule === "clock"

        TextField {
          width: (parent.width - Style.space(8)) / 2
          placeholderText: "Light at HH:MM"
          text: root.config.lightAt || ""
          onEditingFinished: root.saveSettings({ lightAt: text.trim() })
        }

        TextField {
          width: (parent.width - Style.space(8)) / 2
          placeholderText: "Dark at HH:MM"
          text: root.config.darkAt || ""
          onEditingFinished: root.saveSettings({ darkAt: text.trim() })
        }
      }

      PanelSectionHeader {
        text: "Light theme"
        visible: root.config.mode !== "random"
      }

      Dropdown {
        width: parent.width
        visible: root.config.mode !== "random"
        label: "Light"
        value: root.config.light || ""
        options: root.themeOptions("light")
        onChanged: function(value) { root.saveSettings({ light: value }) }
      }

      PanelSectionHeader {
        text: "Dark theme"
        visible: root.config.mode !== "random"
      }

      Dropdown {
        width: parent.width
        visible: root.config.mode !== "random"
        label: "Dark"
        value: root.config.dark || ""
        options: root.themeOptions("dark")
        onChanged: function(value) { root.saveSettings({ dark: value }) }
      }

      PanelSectionHeader {
        text: "Light pool"
        visible: root.config.mode === "random"
      }

      MultiSelect {
        width: parent.width
        visible: root.config.mode === "random"
        label: "Light"
        noSelectionText: "Pick at least one"
        values: root.config.lightPool || []
        options: root.themeOptions("light")
        onChanged: function(values) { root.saveSettings({ lightPool: values }) }
      }

      PanelSectionHeader {
        text: "Dark pool"
        visible: root.config.mode === "random"
      }

      MultiSelect {
        width: parent.width
        visible: root.config.mode === "random"
        label: "Dark"
        noSelectionText: "Pick at least one"
        values: root.config.darkPool || []
        options: root.themeOptions("dark")
        onChanged: function(values) { root.saveSettings({ darkPool: values }) }
      }

      Text {
        width: parent.width
        wrapMode: Text.WordWrap
        color: Qt.darker(Commons.Color.popups.text, 1.4)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        text: root.config.mode === "random"
          ? "A random theme is drawn from each pool at every switch, so the pick changes each day."
          : "Changes take effect at the next switch. Apply now switches immediately."
      }

      Button {
        visible: root.configured
        text: root.periodKind === "light" ? "Switch to dark now" : "Switch to light now"
        onClicked: root.toggleLightDark()
      }

      Text {
        visible: root.configured && root.status.manual === true
        width: parent.width
        wrapMode: Text.WordWrap
        color: Qt.darker(Commons.Color.popups.text, 1.4)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        text: "Held until the next " + (root.status.next && root.status.next.kind === "light" ? "sunrise" : "sunset") + "."
      }

      Button {
        text: "Apply now"
        onClicked: root.applyNow()
      }
    }
  }
}
