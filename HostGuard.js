.pragma library

var HOST_ID = "data-goblin.fileblade"
var HOST_REPOSITORY = "https://github.com/data-goblin/fileblade"
var MISSING_MESSAGE = "This is a FileBlade extension. FileBlade is not installed on your computer, and an extension never installs it for you. Install FileBlade yourself, then this panel goes away."
var DISABLED_MESSAGE = "This is a FileBlade extension. FileBlade is installed but disabled, so this extension has nothing to attach to."

function contributes(manifest) {
  var extensions = manifest && manifest.extensions
  if (!extensions || typeof extensions !== "object") return false
  return Object.keys(extensions).some(function(key) { return key.indexOf(HOST_ID + "/") === 0 })
}

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

function plan(installed, isEnabled, selfId) {
  installed = installed || {}
  var hostInstalled = Object.prototype.hasOwnProperty.call(installed, HOST_ID)
  if (hostInstalled && isEnabled(HOST_ID)) return { show: false, names: [], action: "", command: [], message: "" }
  var waiting = Object.keys(installed).filter(function(id) { return contributes(installed[id]) && isEnabled(id) }).sort()
  return {
    show: waiting.length > 0 && waiting[0] === String(selfId),
    names: waiting.map(function(id) { return String(installed[id].name || id) }),
    action: hostInstalled ? "Enable" : "",
    command: hostInstalled ? enableCommand() : [],
    message: hostInstalled ? DISABLED_MESSAGE : MISSING_MESSAGE
  }
}
