import QtQuick

Item {
  id: service
  visible: false

  property var omarchyPath: null
  property var shell: null
  property var manifest: null
  property var pluginRegistry: null
  property bool retired: false

  readonly property string moduleDir: decodeURIComponent(String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "")).replace(/\/$/, "")
  readonly property var inventory: runtime.item ? runtime.item.inventory : null
  readonly property string error: runtime.status === Loader.Error ? "Provider could not be loaded" : (runtime.item ? runtime.item.error : "")
  readonly property var observers: runtime.item ? runtime.item.observers : []
  readonly property int viewCount: observers.length

  function attach(context) {
    if (retired || !context || !manifest || !manifest.id) return false
    if (String(runtime.source) === "") runtime.setSource(Qt.resolvedUrl("Provider.qml"), {
      providerId: String(manifest.id), providerRoot: moduleDir,
      files: context.service("files"), inventoryComponentUrl: context.ui.url("ArtifactInventory")
    })
    return runtime.item ? runtime.item.attach(context) : false
  }

  function detach(context) {
    if (runtime.item) runtime.item.detach(context)
  }

  function shutdown() {
    retired = true
    if (runtime.item) runtime.item.shutdown()
    runtime.source = ""
  }

  Loader { id: runtime }

  Loader {
    active: !!service.pluginRegistry && !!service.manifest && !!service.manifest.id
    source: "HostGuard.qml"
    onLoaded: {
      item.pluginId = Qt.binding(function() { return service.manifest ? String(service.manifest.id) : "" })
      item.sourceDir = service.moduleDir
    }
  }
}
