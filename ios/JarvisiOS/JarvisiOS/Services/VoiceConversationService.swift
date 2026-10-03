import AVFoundation
import Speech
import SwiftUI

public enum JarvisVoiceState: String {
    case idle = "IDLE", listening = "LISTENING", thinking = "THINKING"
    case speaking = "SPEAKING", error = "ERROR", disconnected = "DISCONNECTED"

    var title: String {
        switch self {
        case .idle: return "Hazır"
        case .listening: return "Dinliyorum"
        case .thinking: return "Yanıt hazırlanıyor"
        case .speaking: return "Konuşuyorum"
        case .error: return "Ses bağlantısını kontrol et"
        case .disconnected: return "Bağlantı bekleniyor"
        }
    }
}

/// Foreground-only speech capture. Voice and text submit through the same chat model.
@MainActor
final class VoiceConversationService: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published var state: JarvisVoiceState = .idle
    @Published var level: Float = 0
    @Published var transcript = ""
    @Published var errorMessage: String?
    @Published var conversationMode = false
    var onTurn: ((String) -> Void)?

    private let engine = AVAudioEngine()
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "tr-TR"))
    private let synthesizer = AVSpeechSynthesizer()
    private var recognition: SFSpeechRecognitionTask?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var player: AVAudioPlayer?
    private var outputURL: URL?
    private var meter: Timer?
    private var silenceTimer: Timer?
    private var lastVoice = Date()
    private var captureStarted = Date()
    private var generation = UUID()
    private var tapped = false
    private var finalizing = false
    private var finishTask: Task<Void, Never>?

    override init() {
        super.init()
        NotificationCenter.default.addObserver(self, selector: #selector(audioInterrupted),
                                               name: AVAudioSession.interruptionNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(routeChanged),
                                               name: AVAudioSession.routeChangeNotification, object: nil)
    }

    deinit { NotificationCenter.default.removeObserver(self) }

    @objc private func audioInterrupted(_ notification: Notification) {
        conversationMode = false
        stop()
    }

    @objc private func routeChanged(_ notification: Notification) {
        if let reason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
           reason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue {
            conversationMode = false
            stop()
        }
    }

    func startListening() async {
        stop()
        let sessionID = UUID()
        generation = sessionID
        let speech = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
        let microphone = await withCheckedContinuation { continuation in
            AVAudioSession.sharedInstance().requestRecordPermission { continuation.resume(returning: $0) }
        }
        guard generation == sessionID else { return }
        guard speech == .authorized, microphone else {
            fail("Mikrofon ve konuşma tanıma iznini Ayarlar’dan açın."); return
        }
        guard let recognizer, recognizer.isAvailable else {
            fail("Türkçe konuşma tanıma şu anda kullanılamıyor."); return
        }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .measurement, options: [.defaultToSpeaker, .allowBluetooth])
            try session.setActive(true)
            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                fail("Kullanılabilir mikrofon bulunamadı."); return
            }
            let request = SFSpeechAudioBufferRecognitionRequest()
            request.shouldReportPartialResults = true
            request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
            self.request = request
            transcript = ""
            errorMessage = nil
            captureStarted = Date()
            lastVoice = Date()
            state = .listening
            recognition = recognizer.recognitionTask(with: request) { [weak self] result, error in
                Task { @MainActor [weak self] in
                    guard let self, self.generation == sessionID else { return }
                    if let result { self.transcript = result.bestTranscription.formattedString }
                    if result?.isFinal == true { self.submitCapturedTurn() }
                    else if let error, !self.finalizing { self.fail(error.localizedDescription) }
                }
            }
            input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
                request.append(buffer)
                guard let samples = buffer.floatChannelData?[0], buffer.frameLength > 0 else { return }
                var sum: Float = 0
                for i in 0..<Int(buffer.frameLength) { sum += samples[i] * samples[i] }
                let rms = sqrt(sum / Float(buffer.frameLength))
                Task { @MainActor [weak self] in
                    guard let self, self.generation == sessionID, self.state == .listening else { return }
                    self.level = min(1, rms * 8)
                    if rms > 0.012 { self.lastVoice = Date() }
                }
            }
            tapped = true
            engine.prepare()
            try engine.start()
            silenceTimer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in
                Task { @MainActor [weak self] in
                    guard let self, self.state == .listening else { return }
                    let now = Date()
                    // Bound every capture, even when the phone is left unattended.
                    if now.timeIntervalSince(self.captureStarted) > 45 ||
                        (self.conversationMode && !self.transcript.isEmpty && now.timeIntervalSince(self.lastVoice) > 1.25) {
                        self.finishListening()
                    }
                }
            }
        } catch { fail(error.localizedDescription) }
    }

    func finishListening() {
        guard state == .listening, !finalizing else { return }
        finalizing = true
        stopCapture()
        request?.endAudio()
        // Give Speech its final result; do not drop the last word when finishing a turn.
        finishTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 900_000_000)
            guard !Task.isCancelled else { return }
            self?.submitCapturedTurn()
        }
    }

    private func submitCapturedTurn() {
        guard state == .listening else { return }
        let text = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        stopCapture()
        finishTask?.cancel()
        request?.endAudio()
        generation = UUID()
        recognition?.cancel()
        recognition = nil
        request = nil
        finalizing = false
        level = 0
        state = text.isEmpty ? .idle : .thinking
        if !text.isEmpty { onTurn?(text) }
    }

    func speak(_ text: String) {
        stop()
        guard !text.isEmpty else { return }
        guard let voice = AVSpeechSynthesisVoice.speechVoices()
            .filter({ $0.language.hasPrefix("tr") })
            .max(by: { $0.quality.rawValue < $1.quality.rawValue }) else {
            fail("Türkçe ses yüklü değil. iPhone ses ayarlarından yükleyin."); return
        }
        state = .thinking
        let id = UUID()
        generation = id
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = voice
        let writer = SpeechAudioWriter()
        synthesizer.write(utterance) { [weak self] buffer in
            let result = writer.append(buffer)
            if let result {
                Task { @MainActor [weak self] in
                    guard let self, self.generation == id else {
                        if case .success(let url) = result { try? FileManager.default.removeItem(at: url) }
                        return
                    }
                    switch result {
                    case .success(let url): self.playSpeech(url)
                    case .failure: self.fail("Ses üretilemedi.")
                    }
                }
            }
        }
    }

    private func playSpeech(_ url: URL) {
        do {
            outputURL = url
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .spokenAudio)
            try session.setActive(true)
            let player = try AVAudioPlayer(contentsOf: url)
            player.isMeteringEnabled = true
            player.delegate = self
            self.player = player
            guard player.play() else { fail("Ses oynatılamadı."); return }
            state = .speaking
            // Measure the currently playing synthesized PCM audio, not elapsed text
            // or an independent animation. AVAudioPlayer meters actual output frames.
            meter = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
                Task { @MainActor [weak self] in
                    guard let self, let player = self.player else { return }
                    player.updateMeters()
                    self.level = min(1, pow(10, player.averagePower(forChannel: 0) / 20) * 4)
                }
            }
        } catch { fail(error.localizedDescription) }
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor [weak self] in
            guard let self, self.player === player else { return }
            self.stop()
            if self.conversationMode && flag { await self.startListening() }
        }
    }

    private func stopCapture() {
        silenceTimer?.invalidate(); silenceTimer = nil
        engine.stop()
        if tapped { engine.inputNode.removeTap(onBus: 0); tapped = false }
    }

    func stop() {
        errorMessage = nil
        generation = UUID()
        finishTask?.cancel(); finishTask = nil
        stopCapture()
        recognition?.cancel(); recognition = nil
        request?.endAudio(); request = nil
        finalizing = false
        synthesizer.stopSpeaking(at: .immediate)
        player?.stop(); player = nil
        meter?.invalidate(); meter = nil
        if let outputURL { try? FileManager.default.removeItem(at: outputURL) }
        outputURL = nil
        level = 0
        state = .idle
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    func fail(_ message: String) {
        stop()
        conversationMode = false
        errorMessage = message
        state = .error
    }
}

/// Speech synthesis buffers are written immediately on their callback thread.
private final class SpeechAudioWriter: @unchecked Sendable {
    private let lock = NSLock()
    private var file: AVAudioFile?
    private let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".caf")
    private var finished = false
    func append(_ buffer: AVAudioBuffer) -> Result<URL, Error>? {
        lock.lock(); defer { lock.unlock() }
        guard !finished, let pcm = buffer as? AVAudioPCMBuffer else { return nil }
        if pcm.frameLength == 0 {
            finished = true
            file = nil
            return .success(url)
        }
        do {
            if file == nil { file = try AVAudioFile(forWriting: url, settings: pcm.format.settings) }
            try file?.write(from: pcm)
            return nil
        } catch {
            finished = true
            file = nil
            try? FileManager.default.removeItem(at: url)
            return .failure(error)
        }
    }
    deinit { if !finished { try? FileManager.default.removeItem(at: url) } }
}
