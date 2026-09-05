import QtQuick

QtObject {
  function accepts(key, modifiers) {
    return key === Qt.Key_R && modifiers === Qt.ShiftModifier
  }
}
