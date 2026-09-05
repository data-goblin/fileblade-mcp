import QtQuick

Item {
  id: service
  property var omarchyPath: null
  property var shell: null
  property var manifest: null
  property var pluginRegistry: null
  visible: false

  readonly property string moduleDir: manifest && manifest.__sourceDir ? String(manifest.__sourceDir) : ""
  readonly property var inventory: runtime.item
  readonly property string error: runtime.status === Loader.Error ? "Shared inventory could not be loaded" : ""
  property var observers: []

  function attach(context) {
    if (!context || observers.indexOf(context) >= 0) return
    observers = observers.concat([context])
    if (String(runtime.source) === "") runtime.setSource(context.ui.url("ArtifactInventory"), {
      files: context.service("files"), providerId: String(manifest.id), providerRoot: moduleDir,
      maximumItems: 1024, itemsKey: "definitions", healthBasis: "configuration-only",
      exactProject: false, scanArguments: ["--watch"],
      observers: Qt.binding(function() { return service.observers })
    })
  }

  function detach(context) {
    observers = observers.filter(function(value) { return value !== context })
  }

  Loader { id: runtime }

  Loader {
    active: !!service.pluginRegistry
    source: "HostGuard.qml"
    onLoaded: {
      item.pluginRegistry = Qt.binding(function() { return service.pluginRegistry })
      item.pluginId = Qt.binding(function() { return service.manifest ? String(service.manifest.id) : "" })
    }
  }
}
