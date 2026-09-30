import Foundation
import Darwin

public final class UnixSocketServer: @unchecked Sendable {
    public let socketPath: String
    private let dispatcher: RPCDispatcher
    private var serverFd: Int32 = -1
    private var isRunning: Bool = false
    private let lock = NSLock()
    private var activeClientsCount: Int = 0

    public var onConnectionStatusChanged: ((Bool) -> Void)?

    public init(socketPath: String = IPCSecurity.defaultSocketPath(), dispatcher: RPCDispatcher) {
        self.socketPath = socketPath
        self.dispatcher = dispatcher
    }

    public func start() throws {
        lock.lock()
        defer { lock.unlock() }

        guard !isRunning else { return }

        try IPCSecurity.prepareSocketDirectory(for: socketPath)

        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else {
            let err = String(cString: strerror(errno))
            throw IPCSecurityError.directoryCreationFailed("Failed to create socket: \(err)")
        }

        var opt: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, socklen_t(MemoryLayout<Int32>.size))
        #if os(macOS)
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &opt, socklen_t(MemoryLayout<Int32>.size))
        #endif

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = socketPath.utf8CString
        guard pathBytes.count < MemoryLayout.size(ofValue: addr.sun_path) else {
            close(fd)
            throw IPCSecurityError.directoryCreationFailed("Socket path too long")
        }

        withUnsafeMutablePointer(to: &addr.sun_path) { ptr in
            ptr.withMemoryRebound(to: CChar.self, capacity: pathBytes.count) { dest in
                _ = pathBytes.withUnsafeBufferPointer { src in
                    memcpy(dest, src.baseAddress!, pathBytes.count)
                }
            }
        }

        let addrLen = socklen_t(MemoryLayout<sockaddr_un>.size)
        let bindResult = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                Darwin.bind(fd, sa, addrLen)
            }
        }

        guard bindResult == 0 else {
            let err = String(cString: strerror(errno))
            close(fd)
            throw IPCSecurityError.directoryCreationFailed("Failed to bind socket: \(err)")
        }

        guard Darwin.listen(fd, 16) == 0 else {
            let err = String(cString: strerror(errno))
            close(fd)
            throw IPCSecurityError.directoryCreationFailed("Failed to listen on socket: \(err)")
        }

        try IPCSecurity.secureSocket(at: socketPath)

        self.serverFd = fd
        self.isRunning = true

        Task.detached { [weak self] in
            self?.acceptLoop(serverFd: fd)
        }
    }

    public func stop() {
        lock.lock()
        defer { lock.unlock() }

        guard isRunning else { return }
        isRunning = false

        if serverFd >= 0 {
            close(serverFd)
            serverFd = -1
        }
        unlink(socketPath)
        notifyConnectionStatus(false)
    }

    private func acceptLoop(serverFd: Int32) {
        while true {
            var clientAddr = sockaddr_un()
            var clientAddrLen = socklen_t(MemoryLayout<sockaddr_un>.size)

            let clientFd = withUnsafeMutablePointer(to: &clientAddr) { ptr in
                ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                    Darwin.accept(serverFd, sa, &clientAddrLen)
                }
            }

            guard clientFd >= 0 else {
                lock.lock()
                let stillRunning = isRunning
                lock.unlock()
                if !stillRunning { break }
                continue
            }

            #if os(macOS)
            var opt: Int32 = 1
            setsockopt(clientFd, SOL_SOCKET, SO_NOSIGPIPE, &opt, socklen_t(MemoryLayout<Int32>.size))
            #endif

            incrementClientCount()

            Task.detached { [weak self] in
                await self?.handleClient(clientFd: clientFd)
                self?.decrementClientCount()
            }
        }
    }

    private func handleClient(clientFd: Int32) async {
        defer {
            close(clientFd)
        }

        var buffer = [UInt8](repeating: 0, count: 4096)
        var lineBuffer = Data()

        while true {
            let bytesRead = read(clientFd, &buffer, buffer.count)
            guard bytesRead > 0 else { break }

            lineBuffer.append(buffer, count: bytesRead)

            while let newlineIndex = lineBuffer.firstIndex(of: UInt8(ascii: "\n")) {
                let lineData = lineBuffer.subdata(in: 0..<newlineIndex)
                lineBuffer.removeSubrange(0...newlineIndex)

                guard !lineData.isEmpty else { continue }

                do {
                    let req = try JSONDecoder().decode(RPCRequest.self, from: lineData)
                    let resp = await dispatcher.dispatch(request: req)
                    var respData = try JSONEncoder().encode(resp)
                    respData.append(UInt8(ascii: "\n"))
                    _ = respData.withUnsafeBytes { raw in
                        write(clientFd, raw.baseAddress!, respData.count)
                    }
                } catch {
                    let errResp = RPCResponse.failure(
                        id: UUID().uuidString,
                        code: "INVALID_REQUEST",
                        message: "Malformed JSON payload: \(error.localizedDescription)"
                    )
                    if var respData = try? JSONEncoder().encode(errResp) {
                        respData.append(UInt8(ascii: "\n"))
                        _ = respData.withUnsafeBytes { raw in
                            write(clientFd, raw.baseAddress!, respData.count)
                        }
                    }
                }
            }
        }
    }

    private func incrementClientCount() {
        lock.lock()
        activeClientsCount += 1
        let count = activeClientsCount
        lock.unlock()
        if count == 1 {
            notifyConnectionStatus(true)
        }
    }

    private func decrementClientCount() {
        lock.lock()
        activeClientsCount = max(0, activeClientsCount - 1)
        let count = activeClientsCount
        lock.unlock()
        if count == 0 {
            notifyConnectionStatus(false)
        }
    }

    private func notifyConnectionStatus(_ isConnected: Bool) {
        DispatchQueue.main.async { [weak self] in
            self?.onConnectionStatusChanged?(isConnected)
        }
    }
}
