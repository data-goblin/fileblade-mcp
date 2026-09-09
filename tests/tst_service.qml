import QtQuick
import QtTest
import ".." as Companion

TestCase {
  name: "CompanionProvider"
  Item { id: files }
  Component { id: providerComponent; Companion.Provider {} }
  Component { id: serviceComponent; Companion.Service { manifest: ({ id: "test.provider" }) } }

  function context() {
    return { service: function(name) { compare(name, "files"); return files },
      ui: { url: function(name) { compare(name, "ArtifactInventory"); return Qt.resolvedUrl("InventoryProbe.qml") } } }
  }

  function provider() {
    return createTemporaryObject(providerComponent, this, {
      providerId: "test.provider", providerRoot: "/plugins/with spaces % and #",
      files: files, inventoryComponentUrl: Qt.resolvedUrl("InventoryProbe.qml")
    })
  }

  function options(inventory) {
    compare(inventory.files, files)
    compare(inventory.providerId, "test.provider")
    compare(inventory.maximumItems, 1024)
    compare(inventory.itemsKey, "definitions")
    compare(inventory.healthBasis, "configuration-only")
    compare(inventory.exactProject, false)
    compare(inventory.scanArguments, ["--watch"])
  }

  function test_host_owned_provider_is_cold_and_shares_inventory() {
    var owner = provider(), first = context(), second = context()
    compare(owner.inventory, null)
    compare(owner.viewCount, 0)
    verify(owner.attach(first))
    var inventory = owner.inventory
    verify(inventory !== null)
    options(inventory)
    compare(inventory.providerRoot, "/plugins/with spaces % and #")
    owner.attach(first)
    compare(owner.viewCount, 1)
    owner.attach(second)
    compare(owner.inventory, inventory)
    compare(inventory.observers.length, 2)
    owner.detach(first)
    compare(inventory.observers.length, 1)
    verify(inventory.ready)
    owner.detach(second)
    compare(inventory.observers.length, 0)
    verify(!inventory.ready)
    owner.attach(first)
    compare(owner.inventory, inventory)
    compare(owner.viewCount, 1)
  }

  function test_shutdown_refuses_late_attachment() {
    var owner = provider(), view = context()
    owner.attach(view)
    owner.shutdown()
    compare(owner.inventory, null)
    compare(owner.viewCount, 0)
    verify(!owner.attach(view))
    compare(owner.inventory, null)
    owner.shutdown()
  }

  function test_unconfigured_provider_stays_cold() {
    var owner = createTemporaryObject(providerComponent, this)
    verify(!owner.attach(context()))
    compare(owner.inventory, null)
    compare(owner.viewCount, 0)
  }

  function test_legacy_service_works_with_stripped_manifest() {
    var service = createTemporaryObject(serviceComponent, this), view = context()
    compare(service.inventory, null)
    compare(service.viewCount, 0)
    compare(service.moduleDir, decodeURIComponent(String(Qt.resolvedUrl("..")).replace(/^file:\/\//, "")).replace(/\/$/, ""))
    verify(service.attach(view))
    options(service.inventory)
    compare(service.inventory.providerRoot, service.moduleDir)
    service.attach(view)
    compare(service.viewCount, 1)
    service.detach(view)
    compare(service.viewCount, 0)
    var inventory = service.inventory
    service.attach(view)
    compare(service.inventory, inventory)
    service.shutdown()
    compare(service.inventory, null)
    verify(!service.attach(view))
  }

  function test_new_host_does_not_start_legacy_inventory() {
    var service = createTemporaryObject(serviceComponent, this), owner = provider()
    owner.attach(context())
    verify(owner.inventory !== null)
    compare(service.inventory, null)
    compare(service.viewCount, 0)
    owner.shutdown()
  }
}
