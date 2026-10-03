import SwiftUI

struct JarvisAvatarView: View {
    let state: JarvisVoiceState
    let level: Float
    var diameter: CGFloat = 172
    var showsState = true
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private var tint: Color {
        switch state {
        case .error: return .red
        case .disconnected: return .gray
        case .listening: return .mint
        case .thinking: return .purple
        default: return .cyan
        }
    }
    var body: some View {
        VStack(spacing: 6) {
            Canvas { context, size in
                drawAvatar(context: &context, size: size)
            }
            .frame(width: diameter, height: diameter)
            .background(RadialGradient(colors: [tint.opacity(0.12), .clear], center: .center, startRadius: 15, endRadius: 100))
            if showsState { Text(state.title).font(.caption).foregroundStyle(tint) }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Jarvis, \(state.title)")
    }
    private func drawAvatar(context: inout GraphicsContext, size: CGSize) {
                let scale = min(size.width, size.height) / 172
                context.scaleBy(x: scale, y: scale)
                let center = CGPoint(x: size.width / (2 * scale), y: size.height / (2 * scale))
                let amplitude = CGFloat(level)
                let radius: CGFloat = 42 + (reduceMotion ? 0 : amplitude * 12)
                for i in 0..<3 {
                    let r = radius + CGFloat(i * 10)
                    let rect = CGRect(x: center.x-r, y: center.y-r, width: r*2, height: r*2)
                    context.stroke(Path(ellipseIn: rect), with: .color(tint.opacity(0.8-Double(i)*0.2)), lineWidth: i == 0 ? 2 : 1)
                }
                for i in 0..<32 {
                    let angle = CGFloat(i) * .pi / 16
                    let r: CGFloat = 65
                    let wave = CGFloat(abs(sin(angle * 3))) * amplitude * 16
                    var ray = Path()
                    ray.move(to: CGPoint(x: center.x + cos(angle)*r, y: center.y + sin(angle)*r))
                    ray.addLine(to: CGPoint(x: center.x + cos(angle)*(r+4+wave), y: center.y + sin(angle)*(r+4+wave)))
                    context.stroke(ray, with: .color(tint.opacity(0.7)), lineWidth: 2)
                }
                // Original geometric face: faceted crown, illuminated eyes and jaw.
                var mask = Path()
                mask.move(to: CGPoint(x: center.x-28, y: center.y-24))
                mask.addLines([CGPoint(x:center.x,y:center.y-36), CGPoint(x:center.x+28,y:center.y-24),
                               CGPoint(x:center.x+23,y:center.y+21), CGPoint(x:center.x,y:center.y+34),
                               CGPoint(x:center.x-23,y:center.y+21)])
                mask.closeSubpath()
                context.stroke(mask, with: .color(tint), lineWidth: 1.5)
                for x: CGFloat in [-18.0, 7.0] {
                    context.fill(Path(roundedRect: CGRect(x:center.x+x,y:center.y-7,width:12,height:3+amplitude*3),cornerRadius:2), with: .color(tint))
                }
                context.fill(Path(roundedRect:CGRect(x:center.x-10,y:center.y+15,width:20,height:2+amplitude*9),cornerRadius:2),with:.color(tint.opacity(0.8)))
    }

}
