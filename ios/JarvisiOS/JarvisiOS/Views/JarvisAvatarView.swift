import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// Iron Man Tony Stark inspired 3D Holographic JARVIS AI Core Avatar.
///
/// Features:
/// - True 3D holographic golden-amber orbital gyroscope sphere matching Tony Stark's lab.
/// - Dynamic 3D inclined orbital gimbal rings, spherical wireframe meridians, and telemetry vernier ticks.
/// - Live reactive audio energy waveforms driven by `level` (speech capture and TTS playback).
/// - Lively state reactions: listening radar sweep, thinking analytical vortex, speaking solar pulse flare.
/// - Interactive tap-responsive energy ripple and haptic feedback.
struct JarvisAvatarView: View {
    let state: JarvisVoiceState
    let level: Float
    var diameter: CGFloat = 172
    var showsState = true

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var tapEnergy: CGFloat = 0.0
    @State private var tapShockwaveRadius: CGFloat = 0.0

    private var active: Bool { [.listening, .thinking, .speaking].contains(state) }

    // MARK: - Holographic Amber/Gold Color Palette
    private var baseAmber: Color {
        switch state {
        case .error:
            return Color(red: 1.0, green: 0.28, blue: 0.15) // Warning crimson-amber
        case .disconnected:
            return Color(red: 0.65, green: 0.50, blue: 0.25) // Low-power standby bronze
        case .listening:
            return Color(red: 1.0, green: 0.78, blue: 0.18) // Active listening luminous gold
        case .thinking:
            return Color(red: 1.0, green: 0.85, blue: 0.25) // High-energy analytical bright amber
        case .speaking:
            return Color(red: 1.0, green: 0.65, blue: 0.08) // Fiery solar speech amber
        default:
            return Color(red: 1.0, green: 0.73, blue: 0.15) // Iconic Tony Stark lab warm gold
        }
    }

    private var coreGold: Color {
        switch state {
        case .error:
            return Color(red: 1.0, green: 0.45, blue: 0.20)
        case .disconnected:
            return Color(red: 0.80, green: 0.65, blue: 0.35)
        default:
            return Color(red: 1.0, green: 0.94, blue: 0.60) // Luminous high-energy singularity
        }
    }

    private var stateSpeedMultiplier: Double {
        switch state {
        case .thinking: return 2.8
        case .speaking: return 1.6
        case .listening: return 1.3
        case .disconnected: return 0.4
        default: return 0.8
        }
    }

    var body: some View {
        VStack(spacing: 12) {
            TimelineView(.animation(minimumInterval: 1.0 / 60)) { timeline in
                let time = timeline.date.timeIntervalSinceReferenceDate
                let phase = reduceMotion ? 0 : time * 0.35 * stateSpeedMultiplier

                Canvas { context, size in
                    drawHologram(context: &context, size: size, phase: phase, time: time)
                }
            }
            .frame(width: diameter, height: diameter)
            .background(
                // Multi-layered holographic radial glow
                ZStack {
                    RadialGradient(
                        colors: [
                            baseAmber.opacity(active ? 0.30 : 0.14),
                            baseAmber.opacity(0.08),
                            .clear
                        ],
                        center: .center,
                        startRadius: 0,
                        endRadius: diameter * 0.65
                    )

                    // Secondary central energy spot
                    RadialGradient(
                        colors: [
                            coreGold.opacity(active ? 0.45 : 0.18),
                            .clear
                        ],
                        center: .center,
                        startRadius: 0,
                        endRadius: diameter * 0.28
                    )
                }
            )
            .contentShape(Circle())
            .onTapGesture {
                triggerTapReaction()
            }

            if showsState {
                HStack(spacing: 6) {
                    Circle()
                        .fill(baseAmber)
                        .frame(width: 5, height: 5)
                        .opacity(active ? 1.0 : 0.6)

                    Text(state.title.uppercased())
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .tracking(3)
                        .foregroundStyle(baseAmber)
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("JARVIS AI Hologram, \(state.title)")
    }

    private func triggerTapReaction() {
        #if canImport(UIKit)
        let generator = UIImpactFeedbackGenerator(style: .medium)
        generator.prepare()
        generator.impactOccurred()
        #endif

        withAnimation(.easeOut(duration: 0.6)) {
            tapEnergy = 1.0
            tapShockwaveRadius = 1.0
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.65) {
            withAnimation(.easeIn(duration: 0.4)) {
                tapEnergy = 0.0
                tapShockwaveRadius = 0.0
            }
        }
    }

    // MARK: - Hologram 3D Canvas Rendering
    private func drawHologram(context: inout GraphicsContext, size: CGSize, phase: Double, time: Double) {
        let scale = min(size.width, size.height) / 260.0
        context.scaleBy(x: scale, y: scale)
        let center = CGPoint(x: 130, y: 130)

        // Dynamic energy modulation
        let rawLevel = CGFloat(max(0, min(1, level)))
        let energy = reduceMotion ? 0 : rawLevel + tapEnergy * 0.45
        let breathing = reduceMotion ? 0 : CGFloat(sin(time * 1.8)) * 3.5

        // Helper: 2D polar point
        func point(radius: CGFloat, angle: Double) -> CGPoint {
            CGPoint(
                x: center.x + radius * CGFloat(cos(angle)),
                y: center.y + radius * CGFloat(sin(angle))
            )
        }

        // Helper: Tilted 3D orbital ellipse point (parametric 3D rotation projection)
        func point3D(radius: CGFloat, angle: Double, tiltAngle: Double, flatten: CGFloat) -> CGPoint {
            let cosA = CGFloat(cos(angle))
            let sinA = CGFloat(sin(angle))
            let cosT = CGFloat(cos(tiltAngle))
            let sinT = CGFloat(sin(tiltAngle))

            // Elliptic projection in local tilted plane
            let localX = radius * cosA
            let localY = radius * sinA * flatten

            // Rotate by tilt angle
            let projX = localX * cosT - localY * sinT
            let projY = localX * sinT + localY * cosT

            return CGPoint(x: center.x + projX, y: center.y + projY)
        }

        // 1. Outer Hologram Boundary & Concentric Calibration Rings
        let outerBaseRadius: CGFloat = 120 + breathing * 0.4 + energy * 6.0
        for (rOffset, opac, width) in [
            (CGFloat(0), 0.22, CGFloat(0.8)),
            (CGFloat(-8), 0.35, CGFloat(1.0)),
            (CGFloat(-22), 0.28, CGFloat(0.8)),
            (CGFloat(-42), 0.40, CGFloat(1.2))
        ] {
            let r = outerBaseRadius + rOffset
            context.stroke(
                Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)),
                with: .color(baseAmber.opacity(opac)),
                lineWidth: width
            )
        }

        // 2. High-Density Vernier Telemetry Marks (96 Radial HUD Ticks)
        let tickRadius: CGFloat = outerBaseRadius - 8
        for i in 0..<96 {
            let angle = Double(i) * (.pi / 48.0)
            let isMajor = (i % 8 == 0)
            let isMedium = (i % 4 == 0)
            let tickLen: CGFloat = isMajor ? 11 : (isMedium ? 6 : 3)
            let tickOpac: Double = isMajor ? 0.90 : (isMedium ? 0.55 : 0.28)

            var tickPath = Path()
            tickPath.move(to: point(radius: tickRadius, angle: angle))
            tickPath.addLine(to: point(radius: tickRadius + tickLen, angle: angle))
            context.stroke(
                tickPath,
                with: .color(baseAmber.opacity(tickOpac)),
                lineWidth: isMajor ? 1.4 : 0.8
            )
        }

        // 3. True 3D Holographic Gyroscope Orbital Rings (Major Iron Man Feature)
        // Multi-axis inclined orbital bands with dynamic angular rotation
        let gyroscopes: [(tilt: Double, flatten: CGFloat, speed: Double, radius: CGFloat, segments: Int)] = [
            (tilt: .pi * 0.18, flatten: 0.42, speed: 0.85, radius: 106, segments: 4),  // Inclined Ring Alpha
            (tilt: -.pi * 0.26, flatten: 0.48, speed: -0.72, radius: 98, segments: 3), // Counter-rotating Ring Beta
            (tilt: .pi * 0.42, flatten: 0.32, speed: 1.15, radius: 90, segments: 4),   // Steep Polar Ring Gamma
            (tilt: -.pi * 0.08, flatten: 0.60, speed: -0.95, radius: 82, segments: 3), // Equatorial Gyro Ring
        ]

        for (idx, gyro) in gyroscopes.enumerated() {
            let gyroPhase = phase * gyro.speed + Double(idx) * 0.75
            let r = gyro.radius + breathing * 0.6 + energy * 4.0

            // Draw segmented 3D orbital trajectory arc
            for seg in 0..<gyro.segments {
                let startAng = gyroPhase + Double(seg) * (2.0 * .pi / Double(gyro.segments))
                let arcSpan = Double.pi * (0.35 + 0.15 * Double(idx % 2))

                var arcPath = Path()
                let steps = 24
                for s in 0...steps {
                    let t = Double(s) / Double(steps)
                    let ang = startAng + t * arcSpan
                    let pt = point3D(radius: r, angle: ang, tiltAngle: gyro.tilt, flatten: gyro.flatten)
                    if s == 0 {
                        arcPath.move(to: pt)
                    } else {
                        arcPath.addLine(to: pt)
                    }
                }

                let isPrimary = (idx == 0 || idx == 2)
                context.stroke(
                    arcPath,
                    with: .color(isPrimary ? coreGold.opacity(0.85) : baseAmber.opacity(0.65)),
                    style: StrokeStyle(lineWidth: isPrimary ? 2.4 : 1.6, lineCap: .round)
                )

                // Highlight head marker of each 3D orbital segment
                let headPt = point3D(radius: r, angle: startAng + arcSpan, tiltAngle: gyro.tilt, flatten: gyro.flatten)
                context.fill(
                    Path(ellipseIn: CGRect(x: headPt.x - 2.5, y: headPt.y - 2.5, width: 5, height: 5)),
                    with: .color(coreGold)
                )
            }
        }

        // 4. Spherical Wireframe Meridians (Holographic Globe Latitude & Longitude)
        for lat in [-0.55, -0.28, 0.28, 0.55] {
            let latRadius = CGFloat(112 * cos(lat))
            let latY = center.y + CGFloat(112 * sin(lat) * 0.42)
            let latRect = CGRect(x: center.x - latRadius, y: latY - 14, width: latRadius * 2, height: 28)
            context.stroke(
                Path(ellipseIn: latRect),
                with: .color(baseAmber.opacity(0.20)),
                lineWidth: 0.7
            )
        }

        // 5. Segmented HUD Radial Soundwave Bars (Audio Energy Reactive)
        let numBars = 48
        for i in 0..<numBars {
            let angle = Double(i) * (.pi / 24.0) + phase * 0.15
            let modulation = CGFloat(0.40 + 0.60 * abs(sin(Double(i) * 2.3 + phase * 3.0)))
            let innerR: CGFloat = 52 + energy * 4.0
            let barLen: CGFloat = 8.0 + modulation * (12.0 + energy * 26.0)

            var barPath = Path()
            barPath.move(to: point(radius: innerR, angle: angle))
            barPath.addLine(to: point(radius: innerR + barLen, angle: angle))

            let barOpac = Double(0.35 + min(0.65, energy * 0.9))
            context.stroke(
                barPath,
                with: .color(coreGold.opacity(barOpac)),
                style: StrokeStyle(lineWidth: 2.2, lineCap: .round)
            )
        }

        // 6. Holographic Particle Nebula & Sparks (24 Orbiting Light Nodes)
        for i in 0..<24 {
            let pAngle = phase * (0.6 + Double(i % 5) * 0.18) + Double(i) * (.pi / 12.0)
            let pRadius: CGFloat = CGFloat(60 + (i * 7) % 55) + energy * 8.0
            let pTilt = Double(i % 4) * (.pi / 4.0)
            let pt = point3D(radius: pRadius, angle: pAngle, tiltAngle: pTilt, flatten: 0.45)

            let pSize: CGFloat = (i % 3 == 0) ? 3.5 : 2.0
            let pAlpha = 0.40 + 0.60 * sin(time * 2.5 + Double(i))
            context.fill(
                Path(ellipseIn: CGRect(x: pt.x - pSize / 2, y: pt.y - pSize / 2, width: pSize, height: pSize)),
                with: .color(coreGold.opacity(max(0.2, pAlpha)))
            )
        }

        // 7. Interactive Tap Energy Shockwave
        if tapShockwaveRadius > 0 {
            let shockR = 40.0 + tapShockwaveRadius * 85.0
            context.stroke(
                Path(ellipseIn: CGRect(x: center.x - shockR, y: center.y - shockR, width: shockR * 2, height: shockR * 2)),
                with: .color(coreGold.opacity(Double(1.0 - tapShockwaveRadius))),
                style: StrokeStyle(lineWidth: 3.5 * (1.0 - tapShockwaveRadius), lineCap: .round)
            )
        }

        // 8. Luminous Central AI Singularity & Core Reactor
        let coreRadius: CGFloat = 38 + breathing * 0.5 + energy * 10.0

        // Outer Core Halo Ring
        context.stroke(
            Path(ellipseIn: CGRect(x: center.x - coreRadius, y: center.y - coreRadius, width: coreRadius * 2, height: coreRadius * 2)),
            with: .color(coreGold.opacity(0.95)),
            lineWidth: 2.0
        )

        // Concentric Core Rings
        let innerCoreRadius = coreRadius * 0.72
        context.stroke(
            Path(ellipseIn: CGRect(x: center.x - innerCoreRadius, y: center.y - innerCoreRadius, width: innerCoreRadius * 2, height: innerCoreRadius * 2)),
            with: .color(baseAmber.opacity(0.85)),
            style: StrokeStyle(lineWidth: 1.4, dash: [4, 3])
        )

        // High-Intensity Center Glow Singularity
        let singularityRadius: CGFloat = 16 + energy * 8.0
        context.fill(
            Path(ellipseIn: CGRect(x: center.x - singularityRadius, y: center.y - singularityRadius, width: singularityRadius * 2, height: singularityRadius * 2)),
            with: .color(coreGold.opacity(0.95))
        )

        // Center Typography
        let titleText = Text("J.A.R.V.I.S.")
            .font(.system(size: 9, weight: .bold, design: .monospaced))
            .foregroundColor(Color.black.opacity(0.9))
        context.draw(titleText, at: center)
    }
}
