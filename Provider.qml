import QtQuick

Item {
  id: provider
  visible: false

  property string providerId: ""
  property string providerRoot: ""
  property var files: null
  property url inventoryComponentUrl: ""
  property bool retired: false
  property var attached: []
  readonly property var observers: attached
  readonly property int viewCount: attached.length
  readonly property var inventory: runtime.item
  readonly property string error: runtime.status === Loader.Error ? "Shared inventory could not be loaded" : ""

  function attach(context) {
    if (retired || !context || !files || !providerId || !providerRoot || !String(inventoryComponentUrl)) return false
    if (attached.indexOf(context) >= 0) return true
    attached = attached.concat([context])
    if (String(runtime.source) === "") runtime.setSource(inventoryComponentUrl, {
      files: files, providerId: providerId, providerRoot: providerRoot,
      maximumItems: 1024, itemsKey: "definitions", healthBasis: "configuration-only",
      exactProject: false, scanArguments: ["--watch"],
      observers: Qt.binding(function() { return provider.observers })
    })
    return true
  }

  function detach(context) {
    attached = attached.filter(function(value) { return value !== context })
  }

  function shutdown() {
    retired = true
    attached = []
    runtime.source = ""
  }

  Loader { id: runtime }
}
