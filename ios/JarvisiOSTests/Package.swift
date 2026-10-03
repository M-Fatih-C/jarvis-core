// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "JarvisiOSTests",
    platforms: [
        .macOS(.v14),
        .iOS(.v17)
    ],
    products: [
        .library(name: "JarvisiOSCore", targets: ["JarvisiOSCore"]),
    ],
    targets: [
        .target(
            name: "JarvisiOSCore",
            path: "Sources/JarvisiOSCore"
        ),
        .testTarget(
            name: "JarvisiOSTests",
            dependencies: ["JarvisiOSCore"],
            path: "Tests/JarvisiOSTests"
        ),
    ]
)
