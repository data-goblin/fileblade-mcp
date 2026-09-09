import QtQuick
import QtTest
import "../HostGuard.js" as HostGuard

TestCase {
  name: "HostGuard"
  readonly property var memory: ({ id: "data-goblin.fileblade-memory", name: "Memory", enabled: true })
  readonly property var skills: ({ id: "data-goblin.fileblade-skills", name: "Skills", enabled: true })

  function snapshot(state, plugins) { return { schemaVersion: 1, state: state, plugins: plugins || [memory, skills] } }

  function test_ready_host_does_not_need_a_shared_registry() {
    compare(HostGuard.plan(snapshot("ready"), memory.id).show, false)
  }

  function test_missing_host_has_no_install_command() {
    var plan = HostGuard.plan(snapshot("missing"), memory.id)
    verify(plan.show)
    compare(plan.command, [])
    compare(plan.action, "")
    verify(plan.message.indexOf("never installs it for you") > 0)
  }

  function test_disabled_host_has_an_explicit_enable_action() {
    var plan = HostGuard.plan(snapshot("disabled"), memory.id)
    verify(plan.show)
    compare(plan.action, "Enable")
    verify(plan.command[2].indexOf("omarchy plugin enable data-goblin.fileblade") >= 0)
  }

  function test_starting_and_unknown_never_offer_install_or_enable() {
    for (var state of ["starting", "unknown"]) {
      var plan = HostGuard.plan(snapshot(state), memory.id)
      verify(plan.show)
      compare(plan.command, [])
      compare(plan.action, "")
      verify(plan.message.indexOf("not installed") === -1)
    }
    verify(HostGuard.plan(snapshot("unknown", []), skills.id).show)
  }

  function test_only_first_enabled_companion_shows() {
    verify(HostGuard.plan(snapshot("missing"), memory.id).show)
    compare(HostGuard.plan(snapshot("missing"), skills.id).show, false)
    compare(HostGuard.plan(snapshot("missing"), memory.id).names, ["Memory", "Skills"])
    var disabledMemory = { id: memory.id, enabled: false }
    verify(HostGuard.plan(snapshot("missing", [disabledMemory, skills]), skills.id).show)
  }

  function test_no_plan_fetches_remote_code() {
    for (var state of ["missing", "disabled", "starting", "unknown", "ready"]) {
      var plan = HostGuard.plan(snapshot(state), memory.id)
      var script = plan.command.join(" ")
      for (var forbidden of ["plugin add", "git", "http"]) verify(script.indexOf(forbidden) === -1)
    }
  }

  function test_no_observation_stays_hidden() {
    verify(!HostGuard.plan(null, memory.id).show)
    verify(!HostGuard.plan({ schemaVersion: 1, state: "invalid" }, memory.id).show)
  }
}
