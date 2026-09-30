import AppKit
import JarvisMacAgentLib

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

let eventStoreService = EventStoreService.shared
let permissionService = PermissionService(eventStore: eventStoreService.eventStore)
let calendarService = CalendarService(eventStore: eventStoreService.eventStore)
let reminderService = ReminderService(eventStore: eventStoreService.eventStore)
let notificationService = NotificationService()

let dispatcher = RPCDispatcher(
    calendarService: calendarService,
    reminderService: reminderService,
    notificationService: notificationService,
    permissionService: permissionService
)

let socketServer = UnixSocketServer(dispatcher: dispatcher)
let statusMenu = StatusMenu(permissionService: permissionService)

socketServer.onConnectionStatusChanged = { isConnected in
    statusMenu.updateCoreConnection(isConnected: isConnected)
}

eventStoreService.onStoreChanged = {
    statusMenu.refreshStatus()
}

do {
    try socketServer.start()
    print("JarvisMacAgent started on socket: \(socketServer.socketPath)")
} catch {
    fputs("Failed to start socket server: \(error)\n", stderr)
    exit(1)
}

statusMenu.setup()
app.run()
