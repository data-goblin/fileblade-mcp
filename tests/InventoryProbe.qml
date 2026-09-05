import QtQuick

Item {
  property var files: null
  property string providerId: ""
  property string providerRoot: ""
  property int maximumItems: 0
  property string itemsKey: ""
  property string healthBasis: ""
  property bool exactProject: true
  property var scanArguments: []
  property var observers: []
}
