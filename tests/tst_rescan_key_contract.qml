import QtQuick
import QtTest
import "../components"

TestCase {
  name: "RescanKeyContract"

  RescanKeyContract { id: contract }

  function test_exact_shift_r_only() {
    verify(contract.accepts(Qt.Key_R, Qt.ShiftModifier))
    verify(!contract.accepts(Qt.Key_R, Qt.NoModifier))
    verify(!contract.accepts(Qt.Key_R, Qt.ControlModifier | Qt.ShiftModifier))
    verify(!contract.accepts(Qt.Key_R, Qt.AltModifier | Qt.ShiftModifier))
    verify(!contract.accepts(Qt.Key_R, Qt.MetaModifier | Qt.ShiftModifier))
  }

  function test_shared_tree_keys_are_not_owned() {
    var keys = [
      Qt.Key_J, Qt.Key_K, Qt.Key_Up, Qt.Key_Down,
      Qt.Key_H, Qt.Key_L, Qt.Key_Left, Qt.Key_Right,
      Qt.Key_G, Qt.Key_Home, Qt.Key_End,
      Qt.Key_PageDown, Qt.Key_PageUp, Qt.Key_Slash,
      Qt.Key_Return, Qt.Key_Enter, Qt.Key_O, Qt.Key_Escape,
      Qt.Key_Tab, Qt.Key_Backtab
    ]
    for (var index = 0; index < keys.length; index++)
      verify(!contract.accepts(keys[index], Qt.NoModifier))
    verify(!contract.accepts(Qt.Key_G, Qt.ShiftModifier))
    verify(!contract.accepts(Qt.Key_D, Qt.ControlModifier))
    verify(!contract.accepts(Qt.Key_U, Qt.ControlModifier))
  }

  function test_host_chords_bubble() {
    verify(!contract.accepts(Qt.Key_Tab, Qt.ControlModifier))
    verify(!contract.accepts(Qt.Key_Backtab, Qt.ControlModifier | Qt.ShiftModifier))
    verify(!contract.accepts(Qt.Key_PageUp, Qt.ControlModifier))
    verify(!contract.accepts(Qt.Key_PageDown, Qt.ControlModifier))
    verify(!contract.accepts(Qt.Key_BracketLeft, Qt.ControlModifier))
    verify(!contract.accepts(Qt.Key_BracketRight, Qt.ControlModifier))
    verify(!contract.accepts(Qt.Key_Z, Qt.AltModifier))
  }
}
