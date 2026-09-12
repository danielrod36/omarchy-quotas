import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "daniel.quotas"
  ipcTarget: "daniel.quotas"
  manageIpc: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color surface: Color.popups.background
  readonly property color track: Style.selectedFillFor(foreground, Color.accent)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // The credential writer that ships beside this file inside the plugin.
  readonly property string configScript: {
    var url = String(Qt.resolvedUrl("config-set"))
    return url.replace(/^file:\/\//, "").replace(/\/$/, "")
  }

  readonly property var providers: usage.enabledProviders
  // The selection follows the provider, not the slot it happens to sit in: a
  // provider whose first scan lands while the panel is open would otherwise
  // shift the list underneath you and swap out what you were reading.
  // An empty id is the overview page: every configured provider in one list.
  property string selectedProviderId: ""
  readonly property bool overview: selectedProviderId === ""
  readonly property int providerIndex: {
    for (var i = 0; i < providers.length; i++)
      if (providers[i].providerId === selectedProviderId) return i
    return 0
  }
  readonly property var provider: (overview || configMode)
    ? null
    : (providers.length > 0 ? providers[providerIndex] : null)

  property bool cursorActive: false
  // The setup page: cards for every provider whose credentials are missing
  // or rejected, with per-provider instructions and credential fields.
  property bool configMode: false

  // Countdowns and "updated" read this instead of Date.now() so the
  // panel keeps telling the truth while it sits open.
  property double nowMs: Date.now()

  readonly property var limits: limitWindows(provider)
  readonly property var models: modelRows(provider)
  readonly property var headline: bindingWindow(provider)
  readonly property var balance: provider ? (provider.balance || null) : null
  // A prepaid account runs low the way a subscription window fills up: the
  // last 10% of the funded credits lights the same alarm.
  readonly property bool balanceAlarming: !!balance && balance.funded > 0
    && balance.remaining / balance.funded <= 0.1

  // The one row a provider gets on the overview page: its binding quota
  // window, or the remaining credit when the provider has no windows.
  function overviewPrimary(p) {
    if (!p) return null
    var window = bindingWindow(p)
    if (window) return { kind: "limit", window: window, percent: window.percent }
    if (p.balance) return { kind: "balance", percent: p.balance.funded > 0 ? p.balance.remaining / p.balance.funded : -1 }
    return null
  }

  // The two numbers a multi-window provider (Kimi, Codex) shows side by side
  // on its overview row: its shortest window (the session) and its longest
  // (the weekly or monthly pool). Single-window and balance providers keep
  // the single meter.
  function splitWindows(p) {
    if (!p) return []
    var windows = []
    var list = p.limits || []
    for (var i = 0; i < list.length; i++) {
      var entry = list[i] || {}
      if (entry.secondary === true) continue
      var percent = Number(entry.percent)
      if (percent < 0) continue
      // resetAt (not resetsAt): resetMsFor reads the panel's window shape.
      windows.push({ label: String(entry.label || ""), title: String(entry.title || windowTitle(entry.label)),
        percent: percent, resetAt: String(entry.resetsAt || ""), span: windowSpanMs(entry.label) })
    }
    if (windows.length < 2) return []
    windows.sort(function(a, b) { return a.span - b.span })
    if (windows[0] === windows[windows.length - 1]) return []
    return [windows[0], windows[windows.length - 1]]
  }

  function providerAlarming(p) {
    var primary = overviewPrimary(p)
    if (!primary) return false
    return primary.kind === "limit" ? primary.percent >= 0.9 : (primary.percent >= 0 && primary.percent <= 0.1)
  }

  readonly property int alarmingCount: {
    var count = 0
    for (var i = 0; i < providers.length; i++)
      if (providerAlarming(providers[i])) count++
    return count
  }

  // In the overview the bar icon lights when any provider is hot; a provider
  // page keeps its own alarm.
  readonly property bool alarming: overview
    ? alarmingCount > 0
    : (!!headline && headline.percent >= 0.9) || balanceAlarming

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)) }
  function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

  function selectProvider(index) {
    if (providers.length === 0) return
    var wrapped = ((index % providers.length) + providers.length) % providers.length
    selectedProviderId = providers[wrapped].providerId
  }

  function selectProviderById(id) {
    selectedProviderId = String(id || "")
  }

  function selectOverview() {
    selectedProviderId = ""
  }

  function refreshNow() {
    usage.refreshAll(true)
  }

  function launchAgent() {
    if (root.bar) root.bar.run("omarchy-agent --pick")
    root.close()
  }

  // ---------------------------------------------------------------- limits
  //
  // Both providers report the same two shapes: a short rolling session window
  // and a long weekly one. Everything below normalizes them into one record so
  // the meters and the hero speak a single language.

  // Claude spells its windows out ("Session (5-hour)"), Codex abbreviates
  // them ("5h window", "30m window"). Both have to land on the same record.
  function windowIsLong(text) {
    return text.indexOf("week") >= 0 || text.indexOf("7-day") >= 0 || text.indexOf("seven") >= 0
      || text.indexOf("month") >= 0 || text.indexOf("30-day") >= 0
  }

  function windowSpanMs(label) {
    var text = String(label || "").toLowerCase()
    if (text.indexOf("month") >= 0 || text.indexOf("30-day") >= 0) return 30 * 24 * 3600 * 1000
    if (windowIsLong(text)) return 7 * 24 * 3600 * 1000
    var hours = text.match(/(\d+)\s*-?\s*h(?:our)?\b/)
    if (hours) return Number(hours[1]) * 3600 * 1000
    var minutes = text.match(/(\d+)\s*-?\s*m(?:in(?:ute)?s?)?\b/)
    if (minutes) return Number(minutes[1]) * 60 * 1000
    return 0
  }

  function windowTitle(label) {
    var text = String(label || "").toLowerCase()
    if (text.indexOf("month") >= 0) return "Monthly"
    if (windowIsLong(text)) return "Weekly"
    if (text.indexOf("session") >= 0 || windowSpanMs(label) > 0) return "Session"
    var plain = String(label || "").replace(/\s*\(.*\)\s*/, "").trim()
    return plain === "" ? "Limit" : plain
  }

  // A collector that already knows which window a limit belongs to says so,
  // and that beats reading it back out of the label: a model-scoped limit is
  // titled after its model, and a name like "Opus 5 (1M context)" would parse
  // as a one-minute window.
  function limitWindow(label, percent, resetAt, title) {
    return {
      title: String(title || "") !== "" ? String(title) : windowTitle(label),
      percent: Number(percent),
      resetAt: String(resetAt || "")
    }
  }

  function limitWindows(p) {
    if (!p) return []
    var out = []
    var list = p.limits || []
    for (var i = 0; i < list.length; i++) {
      var entry = list[i] || {}
      var percent = Number(entry.percent)
      if (percent < 0) continue
      var window = limitWindow(entry.label, percent, entry.resetsAt, entry.title)
      window.secondary = entry.secondary === true
      out.push(window)
    }
    return out
  }

  // The window that decides how much room is left — the fullest one, since
  // that is what stops the next prompt. Secondary lanes (the legacy z.ai and
  // Zhipu monthly MCP tool allowance) only step in when nothing else exists:
  // they are a tools budget, not a token quota.
  function bindingWindow(p) {
    var windows = limitWindows(p)
    var best = null
    var fallback = null
    for (var i = 0; i < windows.length; i++) {
      if (windows[i].secondary === true) {
        if (!fallback || windows[i].percent > fallback.percent) fallback = windows[i]
        continue
      }
      if (!best || windows[i].percent > best.percent) best = windows[i]
    }
    return best || fallback
  }

  function resetMsFor(w) {
    if (!w || w.resetAt === "") return -1
    var ms = new Date(w.resetAt).getTime()
    return isFinite(ms) ? ms - root.nowMs : -1
  }

  function formatDuration(ms) {
    if (!(ms > 0)) return "now"
    var minutes = Math.floor(ms / 60000)
    var hours = Math.floor(minutes / 60)
    var days = Math.floor(hours / 24)
    if (days > 0) return days + "d " + (hours % 24) + "h"
    if (hours > 0) return hours + "h " + (minutes % 60) + "m"
    return Math.max(1, minutes) + "m"
  }

  // ---------------------------------------------------------------- balance
  //
  // Prepaid agents report a credit ledger instead of rate-limit windows: the
  // record's balance object carries remaining, funded, and spent amounts.

  function currencyPrefix(currency) {
    var code = String(currency || "USD").toUpperCase()
    if (code === "USD") return "$"
    if (code === "EUR") return "€"
    if (code === "GBP") return "£"
    return code + " "
  }

  function formatMoney(value, currency) {
    var amount = Number(value)
    if (!isFinite(amount)) amount = 0
    return currencyPrefix(currency) + amount.toFixed(2)
  }

  // Endpoints report fractions (Ollama: 3 decimals); keep one decimal of
  // that precision visible below 10% so "0.7% used" never reads as "0%".
  function formatPercent(value) {
    var percent = value * 100
    if (percent >= 0 && percent < 10) return percent.toFixed(1) + "%"
    return Math.round(percent) + "%"
  }

  function balanceDetailText(b) {
    if (!b || !(b.funded > 0)) return ""
    var text = formatMoney(b.spent, b.currency) + " spent of " + formatMoney(b.funded, b.currency) + " funded"
    if (b.estimated) text += " · estimated"
    return text
  }

  // ---------------------------------------------------------------- content

  // The plan you pay for, under the name of the tool it pays for. Limits live
  // in their own section; the hero just says what this is.
  function heroMeta(p) {
    if (!p) {
      // The overview page: how many providers are being watched, and whether
      // any of them needs attention.
      if (providers.length === 0) return ""
      var noun = providers.length === 1 ? "provider" : "providers"
      return alarmingCount > 0
        ? alarmingCount + (alarmingCount === 1 ? " account" : " accounts") + " running hot"
        : providers.length + " " + noun + " · all clear"
    }
    if (String(p.usageStatusText || "") !== "") return p.usageStatusText
    var tier = String(p.tierLabel || "")
    if (tier === "") return "Subscription"
    return tier.charAt(0).toUpperCase() + tier.slice(1)
  }

  // Local calendar date, recomputed from nowMs so a panel left open across
  // midnight moves the "Today" row with the clock.
  function todayDate() {
    var now = new Date(root.nowMs)
    return now.getFullYear()
      + "-" + String(now.getMonth() + 1).padStart(2, "0")
      + "-" + String(now.getDate()).padStart(2, "0")
  }

  function dayName(date) {
    var parsed = new Date(String(date || "") + "T00:00:00")
    if (isNaN(parsed.getTime())) return String(date || "")
    return ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][parsed.getDay()]
  }

  function dayLabel(date, today) {
    if (today) return "Today"
    return dayName(date)
  }

  function dayTooltip(day, today) {
    if (!day) return ""
    var parsed = new Date(String(day.date) + "T00:00:00")
    var label = isNaN(parsed.getTime())
      ? String(day.date)
      : dayName(day.date) + " " + (parsed.getMonth() + 1) + "/" + parsed.getDate()
    var text = label + " · " + usage.formatTokenCount(Number(day.messageCount || 0)) + " tokens"
    // Prompt and session counts only exist for today, so they ride along here
    // instead of taking a section of their own. Billing-API agents never
    // count prompts, and "0 prompts" would read as a quiet day, not a gap.
    if (today && provider && provider.hasPromptStats !== false)
      text += " · " + Number(provider.todayPrompts || 0) + " prompts · "
        + Number(provider.todaySessions || 0) + " sessions"
    return text
  }

  function weekPeak(p) {
    var days = p ? (p.recentDays || []) : []
    var peak = 0
    for (var i = 0; i < days.length; i++) peak = Math.max(peak, Number(days[i].messageCount || 0))
    return peak
  }

  function modelRows(p) {
    var usageByModel = p ? (p.modelUsage || {}) : {}
    var rows = []
    for (var id in usageByModel) {
      var bucket = usageByModel[id] || {}
      var input = Number(bucket.inputTokens || 0)
      var output = Number(bucket.outputTokens || 0)
      var cacheRead = Number(bucket.cacheReadInputTokens || 0)
      var cacheWrite = Number(bucket.cacheCreationInputTokens || 0)
      rows.push({
        name: usage.friendlyModelName(id),
        total: input + output + cacheRead + cacheWrite,
        input: input,
        output: output,
        cacheRead: cacheRead,
        cacheWrite: cacheWrite
      })
    }
    rows.sort(function(a, b) { return b.total - a.total })
    return rows.slice(0, 4)
  }

  function modelTooltip(row) {
    if (!row) return ""
    return "In " + usage.formatTokenCount(row.input)
      + " · out " + usage.formatTokenCount(row.output)
      + " · cache read " + usage.formatTokenCount(row.cacheRead)
      + " · cache write " + usage.formatTokenCount(row.cacheWrite)
  }

  // Only speaks up when the numbers cover more than this machine.
  // How stale the current provider's numbers are, so a lagging window reads
  // as lag, never as truth.
  function updatedAgoText(p) {
    if (!p) return ""
    var ms = new Date(String(p.updatedAt || "")).getTime()
    if (!isFinite(ms)) return ""
    var age = Math.max(0, root.nowMs - ms)
    var minutes = Math.floor(age / 60000)
    if (minutes < 1) return "Updated just now"
    if (minutes < 60) return "Updated " + minutes + " min ago"
    var hours = Math.floor(minutes / 60)
    return "Updated " + hours + "h " + (minutes % 60) + "m ago"
  }

  function footerText() {
    if (usage.syncStatusText !== "") return usage.syncStatusText
    if (provider && provider.syncEnabled && provider.syncDeviceCount > 0)
      return "Merged from " + provider.syncDeviceCount + " device" + (provider.syncDeviceCount === 1 ? "" : "s")
    return updatedAgoText(provider)
  }

  // Agents that ship a white mark carry an `assets/<id>-light.svg` twin for
  // light surfaces; marks that work on both (Claude's brand-orange) ship one
  // file. The luminance check decides which candidate to try first.
  function colorChannelLuminance(value) {
    var channel = Number(value)
    if (!isFinite(channel)) return 0
    return channel <= 0.03928 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4)
  }

  function colorLuminance(color) {
    return 0.2126 * colorChannelLuminance(color.r)
      + 0.7152 * colorChannelLuminance(color.g)
      + 0.0722 * colorChannelLuminance(color.b)
  }

  // Marks resolve by convention, so a new agent's data file needs nothing
  // from this panel: assets/<id>.svg if it ships one, the module's bar glyph
  // if it doesn't.
  function iconCandidatesForProvider(p, surfaceColor) {
    if (!p) return []
    var candidates = []
    if (colorLuminance(surfaceColor || Color.background) >= 0.5)
      candidates.push(Qt.resolvedUrl("assets/" + p.providerId + "-light.svg"))
    candidates.push(Qt.resolvedUrl("assets/" + p.providerId + ".svg"))
    return candidates
  }


  // Nothing to report, nothing in the bar: Bar.qml collapses a slot whose
  // item is invisible, so the icon appears the moment the first scan finds
  // usage and stays away entirely on a machine with no configured providers.
  visible: providers.length > 0
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    nowMs = Date.now()
    if (panelFlick) panelFlick.contentY = 0
    // Every visit starts on the overview; providers are one click deeper.
    root.selectOverview()
    usage.refreshLimits()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Main {
    id: usage
    settings: root.settings
  }

  // Credential writes from the setup page; the saving SetupCard listens for
  // exit and flips back to the (now configured) provider.
  Process {
    id: configSaveProcess
    running: false

    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("quotas/config", text.trim())
    }
  }

  // Cheap enough to keep running: it only re-evaluates text bindings, and a
  // stale "resets in 2h" on a panel that is open is worse than a timer.
  Timer {
    interval: 30000
    running: root.opened
    repeat: true
    onTriggered: root.nowMs = Date.now()
  }

  // Quota windows move while the panel is being read; re-poll the cheap
  // endpoints every minute instead of waiting out the 5-minute timer.
  Timer {
    interval: 60000
    running: root.opened && !root.configMode
    repeat: true
    onTriggered: usage.refreshLimits()
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refreshNow(); return "ok" }
    function next(): string { root.selectProvider(root.providerIndex + 1); return "ok" }
    function setup(): string { root.open(); root.configMode = true; return "ok" }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󱚣"
    active: root.alarming
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) root.launchAgent()
      else if (buttonCode === Qt.MiddleButton) root.selectProvider(root.providerIndex + 1)
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    // Taller than the control panels on purpose: this one is a dashboard, and
    // the whole point is reading limits and history without scrolling.
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(640))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent

      onMoveRequested: function(dx, dy) {
        if (dx !== 0) {
          root.cursorActive = true
          root.selectProvider(root.providerIndex + dx)
        }
        if (dy !== 0)
          panelFlick.contentY = root.clamp(panelFlick.contentY + dy * Style.space(56), 0,
                                           Math.max(0, panelFlick.contentHeight - panelFlick.height))
      }
      onActivateRequested: root.refreshNow()
      // Esc backs out of a provider page to the overview before it closes.
      onCloseRequested: root.configMode ? (root.configMode = false)
    : root.overview ? root.close()
    : root.selectOverview()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) { if (t === "r" || t === "R") root.refreshNow() }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          // ---------- Hero: provider mark · name · plan ----------
          PanelHero {
            id: hero
            width: parent.width
            title: root.configMode ? "Add provider"
              : root.overview ? "AI Quotas"
              : (root.provider ? root.provider.providerName : "")
            meta: root.configMode
              ? (usage.setupProviders.length > 0 ? "Pick a provider and paste its credential" : "Everything is configured")
              : root.heroMeta(root.provider)
            foreground: root.foreground
            fontFamily: root.fontFamily

            iconComponent: Component {
              ProviderMark {
                provider: root.provider
                markSize: Style.font.display
              }
            }

            // The "+" sits on the hero line, top right of the panel; inside
            // the setup page the same slot turns into the back arrow.
            trailingControl: Component {
              Button {
                property bool back: root.configMode
                readonly property bool offered: !root.configMode && usage.setupProviders.length > 0
                visible: offered || root.configMode
                text: root.configMode ? "󰅖" : "󰐗"
                onClicked: {
                  root.configMode = !root.configMode
                  if (!root.configMode) root.selectOverview()
                }
                foreground: root.foreground
                fontFamily: root.fontFamily
                fontSize: Style.font.icon
                verticalPadding: Style.spacing.controlPaddingY / 2
                horizontalPadding: Style.spacing.controlPaddingX
                bordered: true
              }
            }
          }

          // ---------- Overview: one row per configured provider ----------
          Column {
            visible: root.overview && !root.configMode
            width: parent.width
            spacing: Style.space(12)

            Repeater {
              model: root.providers

              OverviewRow {
                required property var modelData
                width: parent.width
                entry: modelData
                onClicked: {
                  root.cursorActive = false
                  root.selectProviderById(modelData.providerId)
                }
              }
            }
          }

          // ---------- Setup: one card per unconfigured provider ----------
          Column {
            visible: root.configMode
            width: parent.width
            spacing: Style.space(12)

            Repeater {
              model: usage.setupProviders

              SetupCard {
                required property var modelData
                width: parent.width
                entry: modelData
              }
            }
          }

          Text {
            visible: root.overview && root.providers.length === 0
            width: parent.width
            topPadding: Style.space(24)
            text: "No configured providers.\nCredentials land here once omp signs in or quotas.json has a key."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
          }

          // ---------- Provider switch ----------
          // Ten providers do not fit one row on a narrow bar; a Flow wraps
          // the chips instead of squeezing them into clipped fragments.
          Flow {
            id: providerSwitch
            visible: !root.overview && root.providers.length > 1
            width: parent.width
            spacing: Style.spacing.md

            Repeater {
              model: root.providers

              ProviderChip {
                required property var modelData
                required property int index

                provider: modelData
                selected: !root.overview && index === root.providerIndex
                hasCursor: root.cursorActive && index === root.providerIndex
                onClicked: {
                  root.cursorActive = true
                  // Tapping the open provider's chip backs out to the overview.
                  if (!root.overview && index === root.providerIndex) root.selectOverview()
                  else root.selectProvider(index)
                }
                onHovered: function(isHovered) { if (isHovered) root.cursorActive = true }
              }
            }
          }

          // ---------- Status ----------
          BorderSurface {
            visible: !!root.provider && String(root.provider.usageStatusText || "") !== ""
            width: parent.width
            implicitHeight: statusText.implicitHeight + Style.spacing.xl * 2
            color: root.alpha(root.urgent, 0.10)
            borderSpec: Border.flat(root.alpha(root.urgent, 0.35), 1)
            radius: Style.cornerRadius

            Text {
              id: statusText
              textFormat: Text.PlainText
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.space(12)
              anchors.rightMargin: Style.space(12)
              text: root.provider ? String(root.provider.authHelpText || "") : ""
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
          }

          // ---------- Balance / limits ----------
          PanelSeparator {
            visible: balanceSection.visible || limitsSection.visible
            foreground: root.foreground
          }

          Column {
            id: balanceSection
            visible: !!root.balance
            width: parent.width
            spacing: Style.space(10)

            // The meter shows what is left, not what is used: a prepaid
            // account drains toward empty rather than filling toward a cap.
            readonly property real ratio: root.balance && root.balance.funded > 0
              ? root.clamp(root.balance.remaining / root.balance.funded, 0, 1)
              : -1

            PanelSectionHeader {
              width: parent.width
              text: "BALANCE"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Item {
              width: parent.width
              implicitHeight: Math.max(balanceLabel.implicitHeight, balanceValue.implicitHeight)

              Text {
                id: balanceLabel
                text: "Prepaid credits"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
              }

              Text {
                id: balanceValue
                textFormat: Text.PlainText
                text: root.balance ? root.formatMoney(root.balance.remaining, root.balance.currency) : ""
                color: root.balanceAlarming ? root.urgent : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
              }
            }

            Meter {
              visible: balanceSection.ratio >= 0
              width: parent.width
              value: balanceSection.ratio
              alarming: root.balanceAlarming
            }

            Text {
              textFormat: Text.PlainText
              visible: text !== ""
              width: parent.width
              text: root.balanceDetailText(root.balance)
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Column {
            id: limitsSection
            visible: root.limits.length > 0
            width: parent.width
            spacing: Style.space(10)

            PanelSectionHeader {
              text: "LIMITS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Repeater {
              model: root.limits

              LimitRow {
                required property var modelData
                width: limitsSection.width
                window: modelData
              }
            }
          }

          // ---------- Usage ----------
          PanelSeparator {
            visible: usageSection.visible
            foreground: root.foreground
          }

          Column {
            id: usageSection
            visible: !!root.provider && root.provider.recentDays && root.provider.recentDays.length > 0
            width: parent.width
            spacing: Style.spacing.md

            readonly property var days: root.provider ? (root.provider.recentDays || []) : []
            readonly property real peak: Math.max(1, root.weekPeak(root.provider))

            PanelSectionHeader {
              width: parent.width
              text: "TOKENS BY DAY"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Repeater {
              model: usageSection.days

              DayRow {
                required property var modelData
                required property int index

                width: usageSection.width
                day: modelData
                ratio: Number(modelData.messageCount || 0) / usageSection.peak
                // By date, not by position: the Claude stats-cache fallback can
                // hand us a window that stops short of today.
                today: String(modelData.date || "") === root.todayDate()
              }
            }
          }

          // ---------- Models ----------
          PanelSeparator {
            visible: modelSection.visible
            foreground: root.foreground
          }

          Column {
            id: modelSection
            visible: root.models.length > 0
            width: parent.width
            spacing: Style.spacing.md

            PanelSectionHeader {
              width: parent.width
              text: "TOKENS BY MODEL"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Repeater {
              model: root.models

              ModelRow {
                required property var modelData
                width: modelSection.width
                row: modelData
                // Scaled to the heaviest model, so the top row is always full —
                // the same scale-to-peak the weekly chart uses for its busiest day.
                share: modelData.total / Math.max(1, root.models[0].total)
              }
            }
          }

          Text {
            textFormat: Text.PlainText
            visible: text !== ""
            width: parent.width
            topPadding: Style.space(2)
            text: root.footerText()
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
          }
        }
      }
    }
  }

  // A provider's brand mark: assets/<id>.svg (with a -light twin for light
  // surfaces), falling back to the provider's initial when no mark ships.
  component ProviderMark: Item {
    id: mark
    property var provider: null
    property real markSize: Style.font.body

    property var candidates: root.iconCandidatesForProvider(provider, root.surface)
    // Provider objects are rebuilt on every refresh, which churns the array's
    // identity without changing its content. Restart the fallback walk only
    // when the URLs change: re-pointing source at a URL whose load already
    // failed emits no statusChanged, so an identity-only reset would strand
    // the walker on a missing -light twin.
    property string candidatesKey: candidates.join("\n")
    property int candidateIndex: 0
    onCandidatesKeyChanged: candidateIndex = 0

    width: markSize
    height: markSize

    Image {
      id: markImage
      anchors.fill: parent
      source: mark.candidateIndex < mark.candidates.length ? mark.candidates[mark.candidateIndex] : ""
      sourceSize.width: mark.markSize * 2
      sourceSize.height: mark.markSize * 2
      fillMode: Image.PreserveAspectFit
      // Advancing source from inside its own status change trips the
      // binding-loop detector; defer the step one tick.
      onStatusChanged: if (status === Image.Error && mark.candidateIndex < mark.candidates.length)
        Qt.callLater(function() { mark.candidateIndex++ })
    }

    Text {
      textFormat: Text.PlainText
      anchors.centerIn: parent
      visible: markImage.status !== Image.Ready
      text: {
        var name = mark.provider ? String(mark.provider.providerName || "") : ""
        return name.charAt(0).toUpperCase() || button.text
      }
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: mark.markSize * 0.9
      font.bold: true
    }
  }

  // One icon-sized chip in the provider switch row: the brand mark with a
  // selected fill, a hover cursor tint, and the provider's name on hover.
  component ProviderChip: Item {
    id: chip
    property var provider: null
    property bool selected: false
    property bool hasCursor: false
    signal clicked()
    signal hovered(bool isHovered)

    readonly property real side: Math.max(Style.space(30), Style.spacing.controlHeight)
    readonly property bool hot: chipArea.containsMouse || hasCursor

    width: side
    height: side

    Rectangle {
      anchors.fill: parent
      radius: Style.cornerRadius
      color: chip.selected ? root.track : chip.hot ? root.alpha(root.foreground, 0.08) : "transparent"
      border.width: 1
      border.color: chip.selected ? Color.accent : root.alpha(root.foreground, chip.hot ? 0.45 : 0.22)
    }

    ProviderMark {
      provider: chip.provider
      markSize: chip.side * 0.62
      anchors.centerIn: parent
    }

    MouseArea {
      id: chipArea
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: chip.clicked()
      onContainsMouseChanged: chip.hovered(containsMouse)
    }

    PanelToolTip {
      visible: chipArea.containsMouse
      text: chip.provider ? String(chip.provider.providerName || "") : ""
      fontFamily: root.fontFamily
    }
  }

  // A limit window: label and percentage, meter, and reset countdown.
  component LimitRow: Column {
    id: limitRow
    property var window: null

    readonly property bool alarming: window && window.percent >= 0.9

    spacing: Style.space(6)

    Item {
      width: parent.width
      implicitHeight: Math.max(limitLabel.implicitHeight, limitValue.implicitHeight)

      Text {
        id: limitLabel
        textFormat: Text.PlainText
        // A model-scoped window is titled after its model, and those names run
        // long enough to reach the percentage, so the title gives way first.
        text: limitRow.window ? limitRow.window.title : ""
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        elide: Text.ElideRight
        anchors.left: parent.left
        anchors.right: limitValue.left
        anchors.rightMargin: Style.spacing.sm
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        id: limitValue
        textFormat: Text.PlainText
        text: limitRow.window && limitRow.window.percent >= 0
          ? root.formatPercent(limitRow.window.percent)
          : "—"
        color: limitRow.alarming ? root.urgent : root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
      }
    }

    Meter {
      width: parent.width
      value: limitRow.window ? limitRow.window.percent : -1
      alarming: limitRow.alarming
    }

    Text {
      id: resetText
      textFormat: Text.PlainText
      width: parent.width
      text: {
        var remainingMs = root.resetMsFor(limitRow.window)
        return remainingMs > 0 ? "Resets in " + root.formatDuration(remainingMs) : ""
      }
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }
  }

  // Rounded track showing the percentage of the allowance used.
  component Meter: Item {
    id: meter
    property real value: -1
    property bool alarming: false
    property real thickness: Math.max(Style.space(4), Math.round(Style.spacing.controlHeight * 0.14))

    implicitHeight: thickness

    Rectangle {
      id: meterTrack
      anchors.fill: parent
      radius: height / 2
      color: root.track
    }

    Rectangle {
      anchors.left: meterTrack.left
      anchors.verticalCenter: meterTrack.verticalCenter
      height: meterTrack.height
      radius: meterTrack.radius
      width: meterTrack.width * root.clamp(meter.value, 0, 1)
      color: meter.alarming ? root.urgent : root.foreground

      Behavior on width {
        NumberAnimation { duration: 160; easing.type: Easing.OutCubic }
      }
    }

  }

  // One row per day: label, bar, tokens. Today is picked out in full
  // foreground so the week reads as a run-up to right now.
  component DayRow: Item {
    id: dayRow
    property var day: null
    property real ratio: 0
    property bool today: false

    implicitHeight: Math.max(dayLabel.implicitHeight, dayValue.implicitHeight) + Style.spacing.sm

    Text {
      id: dayLabel
      textFormat: Text.PlainText
      text: root.dayLabel(dayRow.day ? dayRow.day.date : "", dayRow.today)
      color: dayRow.today ? root.foreground : root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: dayRow.today
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(52)
    }

    Rectangle {
      id: dayTrack
      anchors.left: dayLabel.right
      anchors.right: dayValue.left
      anchors.leftMargin: Style.space(8)
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      height: Math.max(Style.space(4), Math.round(Style.spacing.controlHeight * 0.14))
      radius: height / 2
      color: root.track

      Rectangle {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        height: parent.height
        radius: parent.radius
        width: parent.width * root.clamp(dayRow.ratio, 0, 1)
        color: dayRow.today ? root.foreground : root.alpha(root.foreground, 0.55)

        Behavior on width {
          NumberAnimation { duration: 160; easing.type: Easing.OutCubic }
        }
      }
    }

    Text {
      id: dayValue
      textFormat: Text.PlainText
      text: usage.formatTokenCount(dayRow.day ? Number(dayRow.day.messageCount || 0) : 0)
      color: dayRow.today ? root.foreground : root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      horizontalAlignment: Text.AlignRight
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(52)
    }

    MouseArea {
      id: dayHover
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.NoButton
    }

    PanelToolTip {
      visible: dayHover.containsMouse
      text: root.dayTooltip(dayRow.day, dayRow.today)
      fontFamily: root.fontFamily
    }
  }

  // Model rows read as a table: the share bar fills the row behind the label
  // instead of stacking under it, which keeps the whole dashboard on one screen.
  component ModelRow: Item {
    id: modelRow
    property var row: null
    property real share: 0

    implicitHeight: modelName.implicitHeight + Style.spacing.lg

    Rectangle {
      anchors.fill: parent
      radius: Style.cornerRadius
      color: root.alpha(root.foreground, 0.05)
    }

    Rectangle {
      anchors.left: parent.left
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      width: parent.width * root.clamp(modelRow.share, 0, 1)
      radius: Style.cornerRadius
      color: root.alpha(root.foreground, 0.14)

      Behavior on width {
        NumberAnimation { duration: 160; easing.type: Easing.OutCubic }
      }
    }

    Text {
      id: modelName
      textFormat: Text.PlainText
      text: modelRow.row ? modelRow.row.name : ""
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
      anchors.left: parent.left
      anchors.leftMargin: Style.space(8)
      anchors.right: modelTokens.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
    }

    Text {
      id: modelTokens
      textFormat: Text.PlainText
      text: modelRow.row ? usage.formatTokenCount(modelRow.row.total) : ""
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: true
      anchors.right: parent.right
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
    }

    MouseArea {
      id: modelHover
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.NoButton
    }

    PanelToolTip {
      visible: modelHover.containsMouse
      text: root.modelTooltip(modelRow.row)
      fontFamily: root.fontFamily
    }
  }

  // One provider's row on the overview page: mark, name, and the single
  // number that matters - the binding quota window's percentage, or the
  // remaining credit when the provider has no windows. The root is an Item
  // (not a Column) so the click-catcher may anchors.fill it.
  component OverviewRow: Item {
    id: row
    property var entry: null
    signal clicked()

    readonly property var primary: root.overviewPrimary(entry)
    readonly property var halves: root.splitWindows(entry)
    // The reset that unblocks the account: while weekly quota remains it is
    // the session window; once the weekly pool is spent, only the weekly
    // reset matters.
    readonly property var nextResetWindow: {
      if (halves.length !== 2) return null
      return halves[1].percent >= 0.999 ? halves[1] : halves[0]
    }
    readonly property bool isBalance: primary && primary.kind === "balance"
    readonly property var bindingLimit: primary && primary.kind === "limit" ? primary.window : null
    readonly property var balance: isBalance && entry ? entry.balance : null
    readonly property real percent: primary ? primary.percent : -1
    readonly property bool alarming: root.providerAlarming(entry)

    implicitHeight: contentCol.implicitHeight

    Column {
      id: contentCol
      width: parent.width
      spacing: Style.space(4)

      Item {
        width: parent.width
        implicitHeight: Math.max(rowMark.implicitHeight, rowLabel.implicitHeight, rowValue.implicitHeight)

        ProviderMark {
          id: rowMark
          provider: row.entry
          markSize: Style.font.body
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
        }

        Text {
          id: rowLabel
          text: row.entry ? row.entry.providerName : ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
          anchors.left: rowMark.right
          anchors.leftMargin: Style.space(8)
          anchors.right: rowValue.left
          anchors.rightMargin: Style.spacing.sm
          anchors.verticalCenter: parent.verticalCenter
        }

        Text {
          id: rowValue
          textFormat: Text.PlainText
          // One right-aligned element on the title line: the binding value
          // for single-window and balance rows, or — for split rows (Kimi,
          // Codex) — the next meaningful reset: the session reset while
          // weekly quota remains, the weekly reset once the pool is spent.
          text: {
            if (row.halves.length === 2) {
              if (!row.nextResetWindow) return ""
              var remainingMs = root.resetMsFor(row.nextResetWindow)
              return remainingMs > 0
                ? row.nextResetWindow.title + " in " + root.formatDuration(remainingMs)
                : ""
            }
            if (row.isBalance)
              return root.formatMoney(row.balance ? row.balance.remaining : 0,
                                      row.balance ? row.balance.currency : "USD")
            return row.percent >= 0 ? root.formatPercent(row.percent) : "-"
          }
          visible: text !== ""
          color: row.halves.length === 2 ? root.dim
            : (row.alarming ? root.urgent : root.foreground)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          font.bold: row.halves.length !== 2
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
        }
      }

      // Session and weekly side by side, the horizontal space halved so
      // both bars fit on one line.
      Row {
        visible: row.halves.length === 2
        width: parent.width
        spacing: Style.space(10)

        Repeater {
          model: row.halves

          Column {
            id: half
            required property var modelData
            width: (parent.width - Style.space(10)) / 2
            spacing: Style.space(3)

            Item {
              width: half.width
              implicitHeight: Math.max(halfLabel.implicitHeight, halfValue.implicitHeight)

              Text {
                id: halfLabel
                text: half.modelData ? half.modelData.title : ""
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
                anchors.left: parent.left
                anchors.right: halfValue.left
                anchors.rightMargin: Style.spacing.sm
                anchors.verticalCenter: parent.verticalCenter
              }

              Text {
                id: halfValue
                textFormat: Text.PlainText
                text: half.modelData ? root.formatPercent(half.modelData.percent) : ""
                color: half.modelData && half.modelData.percent >= 0.9 ? root.urgent : root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
              }
            }

            Meter {
              width: half.width
              value: half.modelData ? half.modelData.percent : -1
              alarming: half.modelData && half.modelData.percent >= 0.9
            }

          }
        }
      }

      Meter {
        visible: row.halves.length !== 2
        width: parent.width
        value: row.percent
        alarming: row.alarming
      }

      Text {
        textFormat: Text.PlainText
        visible: row.halves.length !== 2 && text !== ""
        width: parent.width
        text: {
          if (row.bindingLimit) {
            var remainingMs = root.resetMsFor(row.bindingLimit)
            var caption = row.bindingLimit.title
            if (remainingMs > 0) caption += " \u00B7 resets in " + root.formatDuration(remainingMs)
            return caption
          }
          if (row.balance) return root.balanceDetailText(row.balance)
          // A provider with stored-but-broken credentials still gets its row:
          // say why instead of leaving a bare name over an empty meter.
          if (row.entry && row.entry.usageStatusText) return row.entry.usageStatusText
          return ""
        }
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }

    // Handlers instead of an anchors.fill MouseArea: a filled child of a
    // positioner-parented delegate trips Column's anchor guard.
    TapHandler {
      onTapped: row.clicked()
    }
    HoverHandler {
      cursorShape: Qt.PointingHandCursor
    }
  }

  // One unconfigured provider's setup card: brand mark, name, step-by-step
  // instructions, one field per credential, and a save button that writes
  // quotas.json and refreshes. Providers whose credentials omp owns (Aliyun)
  // show the steps with no field to paste.
  component SetupCard: Column {
    id: card
    property var entry: null
    property string savedEntryId: ""

    readonly property bool hasInputs: entry && entry.setupInputs && entry.setupInputs.length > 0
    spacing: Style.space(6)

    Item {
      width: parent.width
      implicitHeight: Math.max(cardMark.implicitHeight, cardName.implicitHeight)

      ProviderMark {
        id: cardMark
        provider: card.entry
        markSize: Style.font.body
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        id: cardName
        text: card.entry ? card.entry.providerName : ""
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: true
        elide: Text.ElideRight
        anchors.left: cardMark.right
        anchors.leftMargin: Style.space(8)
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
      }
    }

    Column {
      width: parent.width
      spacing: Style.space(2)

      Repeater {
        model: card.entry ? card.entry.setupGuide : []

        Text {
          required property var modelData
          textFormat: Text.PlainText
          width: parent.width
          text: String(modelData)
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
      }
    }

    Column {
      visible: card.hasInputs
      width: parent.width
      spacing: Style.space(6)

      Repeater {
        id: fieldsRepeater
        model: card.entry ? card.entry.setupInputs : []

        TextField {
          id: field
          required property var modelData
          readonly property string fieldKey: modelData ? String(modelData.key) : ""
          width: parent.width
          placeholderText: modelData ? String(modelData.label || modelData.key || "") : ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }

      Button {
        text: "Save"
        onClicked: {
          if (!card.entry) return
          var fields = card.entry.setupInputs
          var command = [root.configScript, card.entry.setupSection]
          var complete = true
          for (var i = 0; i < fields.length; i++) {
            var value = String(card.valueFor(fields[i].key)).trim()
            if (value === "") complete = false
            command.push(String(fields[i].key))
            command.push(value)
          }
          if (!complete) return
          card.savedEntryId = card.entry.providerId
          root.configSaveProcess.command = command
          root.configSaveProcess.running = true
        }
        foreground: root.foreground
        fontFamily: root.fontFamily
        fontSize: Style.font.caption
      }
    }

    function valueFor(key) {
      for (var i = 0; i < fieldsRepeater.count; i++) {
        var item = fieldsRepeater.itemAt(i)
        if (item && item.fieldKey === key) return item.text
      }
      return ""
    }

    Connections {
      target: configSaveProcess
      function onExited(exitCode) {
        if (!card.savedEntryId || exitCode !== 0) return
        var savedId = card.savedEntryId
        card.savedEntryId = ""
        root.configMode = false
        usage.refreshAll(true)
        root.selectProviderById(savedId)
      }
    }
  }

}
