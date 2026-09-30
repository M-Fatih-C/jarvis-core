import Foundation
import Darwin

public enum IPCSecurityError: Error, LocalizedError {
    case directoryCreationFailed(String)
    case permissionSettingFailed(String)
    case staleSocketCleanupFailed(String)

    public var errorDescription: String? {
        switch self {
        case .directoryCreationFailed(let msg): return "Directory creation failed: \(msg)"
        case .permissionSettingFailed(let msg): return "Setting permissions failed: \(msg)"
        case .staleSocketCleanupFailed(let msg): return "Stale socket cleanup failed: \(msg)"
        }
    }
}

public struct IPCSecurity {
    public static func defaultSocketPath() -> String {
        let uid = getuid()
        return "/tmp/jarvis-\(uid)/mac-agent.sock"
    }

    public static func prepareSocketDirectory(for socketPath: String) throws {
        let url = URL(fileURLWithPath: socketPath)
        let parentDir = url.deletingLastPathComponent().path

        var isDir: ObjCBool = false
        if !FileManager.default.fileExists(atPath: parentDir, isDirectory: &isDir) {
            let res = mkdir(parentDir, S_IRWXU) // 0700
            if res != 0 {
                let err = String(cString: strerror(errno))
                throw IPCSecurityError.directoryCreationFailed(err)
            }
        } else {
            // Enforce 0700
            let res = chmod(parentDir, S_IRWXU)
            if res != 0 {
                let err = String(cString: strerror(errno))
                throw IPCSecurityError.permissionSettingFailed(err)
            }
        }

        // Clean up stale socket file if present
        if FileManager.default.fileExists(atPath: socketPath) {
            let res = unlink(socketPath)
            if res != 0 && errno != ENOENT {
                let err = String(cString: strerror(errno))
                throw IPCSecurityError.staleSocketCleanupFailed(err)
            }
        }
    }

    public static func secureSocket(at path: String) throws {
        let res = chmod(path, S_IRUSR | S_IWUSR) // 0600
        if res != 0 {
            let err = String(cString: strerror(errno))
            throw IPCSecurityError.permissionSettingFailed(err)
        }
    }
}
