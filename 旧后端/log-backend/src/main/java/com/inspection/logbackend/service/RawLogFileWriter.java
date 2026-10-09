package com.inspection.logbackend.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Arrays;

@Service
@Slf4j
public class RawLogFileWriter {

    private static final byte[] RECORD_SEPARATOR = "\n".getBytes(StandardCharsets.UTF_8);

    private final Path windowsRawLogFile;
    private final Path linuxRawLogFile;
    private final Object writeLock = new Object();

    public RawLogFileWriter(
            @Value("${log-storage.windows-file:data/windows/raw-logs.log}") String windowsRawLogFile,
            @Value("${log-storage.linux-file:data/linux/raw-logs.log}") String linuxRawLogFile) {
        this.windowsRawLogFile = toAbsolutePath(windowsRawLogFile);
        this.linuxRawLogFile = toAbsolutePath(linuxRawLogFile);
        log.info("Raw log storage configured: Windows={}, Linux={}",
                this.windowsRawLogFile, this.linuxRawLogFile);
    }

    public void appendWindows(String rawContent) {
        append(windowsRawLogFile, rawContent);
    }

    public void appendLinux(String rawContent) {
        append(linuxRawLogFile, rawContent);
    }

    private void append(Path rawLogFile, String rawContent) {
        byte[] content = rawContent.getBytes(StandardCharsets.UTF_8);
        byte[] record = content.length == 0 || content[content.length - 1] != '\n'
                ? Arrays.copyOf(content, content.length + RECORD_SEPARATOR.length)
                : content;

        if (record != content) {
            record[record.length - 1] = RECORD_SEPARATOR[0];
        }

        synchronized (writeLock) {
            try {
                Path parent = rawLogFile.getParent();
                if (parent != null) {
                    Files.createDirectories(parent);
                }

                Files.write(rawLogFile, record,
                        StandardOpenOption.CREATE,
                        StandardOpenOption.WRITE,
                        StandardOpenOption.APPEND);
                log.info("Raw log saved: file={}, bytes={}", rawLogFile, record.length);
            } catch (IOException exception) {
                throw new UncheckedIOException("Failed to persist raw logs to " + rawLogFile, exception);
            }
        }
    }

    private Path toAbsolutePath(String file) {
        return Path.of(file).toAbsolutePath().normalize();
    }
}
