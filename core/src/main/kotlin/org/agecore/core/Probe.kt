package org.agecore.core

/** Toolchain probe: confirms the shared core compiles and runs on the JVM. */
object Probe {
    const val SCHEMA = "agecore.count/1"
    fun greet(): String = "agecore core ok on ${System.getProperty("java.version")}"
}
