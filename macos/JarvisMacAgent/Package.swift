// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "JarvisMacAgent",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(
            name: "JarvisMacAgent",
            targets: ["JarvisMacAgent"]
        ),
        .library(
            name: "JarvisMacAgentLib",
            targets: ["JarvisMacAgentLib"]
        )
    ],
    dependencies: [],
    targets: [
        .target(
            name: "JarvisMacAgentLib",
            dependencies: [],
            path: "Sources/JarvisMacAgentLib"
        ),
        .executableTarget(
            name: "JarvisMacAgent",
            dependencies: ["JarvisMacAgentLib"],
            path: "Sources/JarvisMacAgent"
        ),
        .testTarget(
            name: "JarvisMacAgentTests",
            dependencies: ["JarvisMacAgentLib"],
            path: "Tests/JarvisMacAgentTests"
        )
    ]
)
