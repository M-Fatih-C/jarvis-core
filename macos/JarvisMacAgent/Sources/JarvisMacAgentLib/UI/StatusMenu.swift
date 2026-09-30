import AppKit
import ServiceManagement

public final class StatusMenu: NSObject, NSMenuDelegate, @unchecked Sendable {
    private var statusItem: NSStatusItem?
    private let permissionService: PermissionServiceProtocol
    private var isCoreConnected: Bool = false

    private let titleMenuItem = NSMenuItem(title: "Jarvis MacAgent", action: nil, keyEquivalent: "")
    private let coreStatusMenuItem = NSMenuItem(title: "Core: ○ Disconnected", action: nil, keyEquivalent: "")
    private let calendarStatusMenuItem = NSMenuItem(title: "Calendar: ? Not Requested", action: nil, keyEquivalent: "")
    private let remindersStatusMenuItem = NSMenuItem(title: "Reminders: ? Not Requested", action: nil, keyEquivalent: "")
    private let notificationsStatusMenuItem = NSMenuItem(title: "Notifications: ? Not Requested", action: nil, keyEquivalent: "")
    private let startAtLoginMenuItem = NSMenuItem(title: "Start at Login", action: #selector(toggleStartAtLogin), keyEquivalent: "")

    public init(permissionService: PermissionServiceProtocol) {
        self.permissionService = permissionService
        super.init()
    }

    public func setup() {
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            if let button = self.statusItem?.button {
                button.title = "⚡️ Jarvis"
            }

            let menu = NSMenu()
            menu.delegate = self

            self.titleMenuItem.isEnabled = false
            menu.addItem(self.titleMenuItem)
            menu.addItem(NSMenuItem.separator())

            self.coreStatusMenuItem.isEnabled = false
            menu.addItem(self.coreStatusMenuItem)

            self.calendarStatusMenuItem.isEnabled = false
            menu.addItem(self.calendarStatusMenuItem)

            self.remindersStatusMenuItem.isEnabled = false
            menu.addItem(self.remindersStatusMenuItem)

            self.notificationsStatusMenuItem.isEnabled = false
            menu.addItem(self.notificationsStatusMenuItem)

            menu.addItem(NSMenuItem.separator())

            let grantCalItem = NSMenuItem(title: "Grant Calendar Access", action: #selector(self.requestCalendarAccessAction), keyEquivalent: "")
            grantCalItem.target = self
            menu.addItem(grantCalItem)

            let grantRemItem = NSMenuItem(title: "Grant Reminders Access", action: #selector(self.requestRemindersAccessAction), keyEquivalent: "")
            grantRemItem.target = self
            menu.addItem(grantRemItem)

            let grantNotifItem = NSMenuItem(title: "Grant Notification Access", action: #selector(self.requestNotificationAccessAction), keyEquivalent: "")
            grantNotifItem.target = self
            menu.addItem(grantNotifItem)

            menu.addItem(NSMenuItem.separator())

            self.startAtLoginMenuItem.target = self
            self.updateStartAtLoginState()
            menu.addItem(self.startAtLoginMenuItem)

            let quitItem = NSMenuItem(title: "Quit MacAgent", action: #selector(self.quitAction), keyEquivalent: "q")
            quitItem.target = self
            menu.addItem(quitItem)

            self.statusItem?.menu = menu
            self.refreshStatus()
        }
    }

    public func updateCoreConnection(isConnected: Bool) {
        self.isCoreConnected = isConnected
        DispatchQueue.main.async { [weak self] in
            self?.refreshStatus()
        }
    }

    public func refreshStatus() {
        coreStatusMenuItem.title = isCoreConnected ? "Core: ● Connected" : "Core: ○ Disconnected"

        let calStatus = permissionService.calendarStatus()
        switch calStatus {
        case .fullAccess:
            calendarStatusMenuItem.title = "Calendar: ✓ Full Access"
        case .denied, .restricted:
            calendarStatusMenuItem.title = "Calendar: ✕ Denied"
        default:
            calendarStatusMenuItem.title = "Calendar: ? Not Requested"
        }

        let remStatus = permissionService.remindersStatus()
        switch remStatus {
        case .fullAccess:
            remindersStatusMenuItem.title = "Reminders: ✓ Full Access"
        case .denied, .restricted:
            remindersStatusMenuItem.title = "Reminders: ✕ Denied"
        default:
            remindersStatusMenuItem.title = "Reminders: ? Not Requested"
        }

        Task { @MainActor [weak self] in
            guard let self = self else { return }
            let notifStatus = await self.permissionService.notificationStatus()
            switch notifStatus {
            case .authorized:
                self.notificationsStatusMenuItem.title = "Notifications: ✓ Enabled"
            case .denied:
                self.notificationsStatusMenuItem.title = "Notifications: ✕ Disabled"
            default:
                self.notificationsStatusMenuItem.title = "Notifications: ? Not Requested"
            }
        }
    }

    public func menuWillOpen(_ menu: NSMenu) {
        refreshStatus()
        updateStartAtLoginState()
    }

    @objc private func requestCalendarAccessAction() {
        Task {
            _ = await permissionService.requestCalendarAccess()
            await MainActor.run { [weak self] in
                self?.refreshStatus()
            }
        }
    }

    @objc private func requestRemindersAccessAction() {
        Task {
            _ = await permissionService.requestRemindersAccess()
            await MainActor.run { [weak self] in
                self?.refreshStatus()
            }
        }
    }

    @objc private func requestNotificationAccessAction() {
        Task {
            _ = await permissionService.requestNotificationAccess()
            await MainActor.run { [weak self] in
                self?.refreshStatus()
            }
        }
    }

    @objc private func toggleStartAtLogin() {
        if #available(macOS 13.0, *) {
            let service = SMAppService.mainApp
            if service.status == .enabled {
                try? service.unregister()
            } else {
                try? service.register()
            }
            updateStartAtLoginState()
        }
    }

    private func updateStartAtLoginState() {
        if #available(macOS 13.0, *) {
            let isEnabled = (SMAppService.mainApp.status == .enabled)
            startAtLoginMenuItem.state = isEnabled ? .on : .off
        } else {
            startAtLoginMenuItem.state = .off
        }
    }

    @objc private func quitAction() {
        NSApplication.shared.terminate(nil)
    }
}
