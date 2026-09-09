import QtQuick

Item {
  property var files: null
  property string providerId: ""
  property string providerRoot: ""
  property int maximumItems: 1000
  property string itemsKey: "items"
  property string healthBasis: ""
  property bool exactProject: true
  property var scanArguments: []
  property var observers: []
  readonly property bool ready: observers.length > 0
}
