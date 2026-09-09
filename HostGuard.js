.pragma library

var HOST_ID = "data-goblin.fileblade"
var HOST_REPOSITORY = "https://github.com/data-goblin/fileblade"
var MISSING_MESSAGE = "This is a FileBlade extension. FileBlade is not installed on your computer, and an extension never installs it for you. Install FileBlade yourself, then this panel goes away."
var DISABLED_MESSAGE = "This is a FileBlade extension. FileBlade is installed but disabled, so this extension has nothing to attach to."
var STARTING_MESSAGE = "FileBlade is enabled but is not responding yet. Wait for it to start, or restart the shell if it stays unavailable."
var UNKNOWN_MESSAGE = "FileBlade availability could not be checked. The check will retry automatically."

function enableCommand() {
  var script = [
    "out=$(omarchy plugin enable " + HOST_ID + " 2>&1) || {",
    "  notify-send -u critical 'FileBlade could not be enabled' \"$(printf '%s\\n' \"$out\" | tail -n 1)\"",
    "  omarchy-shell shell rescanPlugins",
    "  exit 1",
    "}",
    "for _ in $(seq 1 100); do",
    "  jq -e 'any(.plugins[]?; .id == \"" + HOST_ID + "\")' \"$HOME/.config/omarchy/shell.json\" >/dev/null 2>&1 &&",
    "    omarchy-shell " + HOST_ID + " status >/dev/null 2>&1 && break",
    "  sleep 0.1",
    "done",
    "sleep 1",
    "exec omarchy restart shell"
  ]
  return ["sh", "-c", script.join("\n")]
}

function plan(snapshot, selfId) {
  var hidden = { show: false, names: [], action: "", command: [], message: "" }
  if (!snapshot || snapshot.schemaVersion !== 1 || snapshot.state === "ready") return hidden
  if (["missing", "disabled", "starting", "unknown"].indexOf(snapshot.state) < 0) return hidden
  var waiting = Array.isArray(snapshot.plugins) ? snapshot.plugins.filter(function(row) {
    return row && row.enabled === true && typeof row.id === "string"
  }).sort(function(a, b) { return a.id < b.id ? -1 : a.id > b.id ? 1 : 0 }) : []
  if (snapshot.state === "unknown" && waiting.length === 0 && selfId)
    waiting = [{ id: String(selfId), name: String(selfId) }]
  var disabled = snapshot.state === "disabled"
  return {
    show: waiting.length > 0 && waiting[0].id === String(selfId),
    names: waiting.map(function(row) { return String(row.name || row.id) }),
    action: disabled ? "Enable" : "",
    command: disabled ? enableCommand() : [],
    message: disabled ? DISABLED_MESSAGE : snapshot.state === "missing" ? MISSING_MESSAGE
      : snapshot.state === "starting" ? STARTING_MESSAGE : UNKNOWN_MESSAGE
  }
}
