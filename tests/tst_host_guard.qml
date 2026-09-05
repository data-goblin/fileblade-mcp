import QtQuick
import QtTest
import "../HostGuard.js" as HostGuard

TestCase {
  name: "HostGuard"

  readonly property var host: ({ id: "data-goblin.fileblade", name: "FileBlade" })
  readonly property var skills: ({ id: "data-goblin.fileblade-skills", name: "Skills", extensions: { "data-goblin.fileblade/blade": [] } })
  readonly property var memory: ({ id: "data-goblin.fileblade-memory", name: "Memory", extensions: { "data-goblin.fileblade/helper": [] } })
  readonly property var weather: ({ id: "acme.weather", name: "Weather", extensions: { "acme.other/thing": [] } })

  function enabledExcept(disabled) {
    return function(id) { return disabled.indexOf(id) === -1 }
  }

  function test_host_enabled_shows_nothing() {
    var plan = HostGuard.plan({ "data-goblin.fileblade": host, "data-goblin.fileblade-skills": skills }, enabledExcept([]), "data-goblin.fileblade-skills")
    compare(plan.show, false)
  }

  function test_host_missing_offers_install() {
    var plan = HostGuard.plan({ "data-goblin.fileblade-skills": skills, "acme.weather": weather }, enabledExcept([]), "data-goblin.fileblade-skills")
    compare(plan.show, true)
    compare(plan.action, "Install")
    compare(plan.command.slice(0, 2), ["sh", "-c"])
    verify(plan.command[2].indexOf("omarchy plugin add https://github.com/data-goblin/fileblade.git --enable --yes 2>&1") > 0)
    verify(plan.command[2].indexOf("exec omarchy restart shell") > 0)
    verify(plan.command[2].indexOf("omarchy-shell shell rescanPlugins") > 0)
    compare(plan.names, ["Skills"])
  }

  function test_only_first_extension_shows_and_lists_all() {
    var installed = { "data-goblin.fileblade-skills": skills, "data-goblin.fileblade-memory": memory }
    compare(HostGuard.plan(installed, enabledExcept([]), "data-goblin.fileblade-memory").show, true)
    compare(HostGuard.plan(installed, enabledExcept([]), "data-goblin.fileblade-skills").show, false)
    compare(HostGuard.plan(installed, enabledExcept([]), "data-goblin.fileblade-memory").names, ["Memory", "Skills"])
    compare(HostGuard.plan(installed, enabledExcept(["data-goblin.fileblade-memory"]), "data-goblin.fileblade-skills").show, true)
  }

  function test_host_installed_but_disabled_offers_enable() {
    var plan = HostGuard.plan({ "data-goblin.fileblade": host, "data-goblin.fileblade-skills": skills }, enabledExcept(["data-goblin.fileblade"]), "data-goblin.fileblade-skills")
    compare(plan.show, true)
    compare(plan.action, "Enable")
    verify(plan.command[2].indexOf("omarchy plugin enable data-goblin.fileblade 2>&1") > 0)
  }

  function test_no_registry_data_shows_nothing() {
    compare(HostGuard.plan(null, enabledExcept([]), "data-goblin.fileblade-skills").show, false)
  }
}
