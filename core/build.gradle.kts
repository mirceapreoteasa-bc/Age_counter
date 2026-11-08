plugins { kotlin("jvm") }
kotlin { jvmToolchain(21) }
dependencies { testImplementation(kotlin("test")) }
tasks.test {
    useJUnitPlatform()
    // The real-photo checks decode a full 24 Mpx JPEG and its working copy.
    maxHeapSize = "1536m"
    testLogging { showStandardStreams = true }
}
