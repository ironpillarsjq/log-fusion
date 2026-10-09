package com.inspection.logbackend.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class RawLogFileWriterTests {

    @TempDir
    Path tempDirectory;

    @Test
    void appendsWindowsAndLinuxLogsToDifferentFiles() throws IOException {
        Path windowsFile = tempDirectory.resolve("windows/raw-logs.log");
        Path linuxFile = tempDirectory.resolve("linux/raw-logs.log");
        RawLogFileWriter writer = new RawLogFileWriter(windowsFile.toString(), linuxFile.toString());

        writer.appendWindows("[{\"EventID\":4624}]");
        writer.appendLinux("[{\"message\":\"第二条日志\"}]\n");

        assertThat(Files.readString(windowsFile)).isEqualTo("[{\"EventID\":4624}]\n");
        assertThat(Files.readString(linuxFile)).isEqualTo("[{\"message\":\"第二条日志\"}]\n");
    }
}
