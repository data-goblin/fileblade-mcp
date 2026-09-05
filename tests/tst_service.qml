import QtQuick
import QtTest
import ".." as Mcp

TestCase {
  name: "McpProviderService"
  Item { id: files }
  Component { id: providerComponent; Mcp.Service { manifest: ({ id: "test.mcp", __sourceDir: "/plugins/mcp" }) } }

  function test_cold_load_and_shared_observers() {
    var provider = createTemporaryObject(providerComponent, this)
    var view = { service: function() { return files }, ui: { url: function(name) {
      compare(name, "ArtifactInventory")
      return Qt.resolvedUrl("InventoryProbe.qml")
    } } }
    compare(provider.inventory, null)
    provider.attach(view)
    verify(provider.inventory !== null)
    compare(provider.inventory.files, files)
    compare(provider.inventory.providerId, "test.mcp")
    compare(provider.inventory.providerRoot, "/plugins/mcp")
    compare(provider.inventory.maximumItems, 1024)
    compare(provider.inventory.itemsKey, "definitions")
    compare(provider.inventory.healthBasis, "configuration-only")
    verify(!provider.inventory.exactProject)
    compare(provider.inventory.scanArguments, ["--watch"])
    var inventory = provider.inventory
    provider.attach(view)
    compare(inventory.observers.length, 1)
    var second = { ui: view.ui, service: view.service }
    provider.attach(second)
    compare(inventory.observers.length, 2)
    provider.detach(view); provider.detach(second)
    compare(inventory.observers.length, 0)
    provider.attach(view)
    compare(provider.inventory, inventory)
    compare(inventory.observers.length, 1)
  }
}
