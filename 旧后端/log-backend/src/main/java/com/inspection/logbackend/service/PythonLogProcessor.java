package com.inspection.logbackend.service;

import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

@Service
public class PythonLogProcessor {

    private static final TypeReference<List<Map<String, Object>>> RESULT_TYPE = new TypeReference<>() {
    };

    private final ObjectMapper objectMapper;
    private final String pythonCommand;
    private final Path windowsScript;
    private final Path linuxScript;
    private final long timeoutSeconds;

    public PythonLogProcessor(
            ObjectMapper objectMapper,
            @Value("${log-processing.python-command:python}") String pythonCommand,
            @Value("${log-processing.windows-script:${user.dir}/src/main/python/windows/process_logs.py}") String windowsScript,
            @Value("${log-processing.linux-script:${user.dir}/src/main/python/linux/process_logs.py}") String linuxScript,
            @Value("${log-processing.timeout-seconds:30}") long timeoutSeconds) {
        this.objectMapper = objectMapper;
        this.pythonCommand = pythonCommand;
        this.windowsScript = Path.of(windowsScript).toAbsolutePath().normalize();
        this.linuxScript = Path.of(linuxScript).toAbsolutePath().normalize();
        this.timeoutSeconds = timeoutSeconds;
    }

    public List<Map<String, Object>> process(Platform platform, String rawBody) {
        Path script = platform == Platform.WINDOWS ? windowsScript : linuxScript;
        if (!Files.isRegularFile(script)) {
            throw new LogProcessingException("Python processor script does not exist: " + script);
        }

        Process process = null;
        try {
            process = new ProcessBuilder(pythonCommand, script.toString())
                    .redirectErrorStream(true)
                    .start();
            Process currentProcess = process;
            CompletableFuture<byte[]> outputFuture = CompletableFuture.supplyAsync(
                    () -> readOutput(currentProcess));

            try (OutputStream input = process.getOutputStream()) {
                input.write(rawBody.getBytes(StandardCharsets.UTF_8));
            }

            if (!process.waitFor(timeoutSeconds, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                throw new LogProcessingException("Python log processor timed out after " + timeoutSeconds + " seconds");
            }

            String output = new String(outputFuture.get(5, TimeUnit.SECONDS), StandardCharsets.UTF_8);
            if (process.exitValue() != 0) {
                throw new LogProcessingException("Python log processor failed: " + output.trim());
            }
            return objectMapper.readValue(output, RESULT_TYPE);
        } catch (LogProcessingException exception) {
            throw exception;
        } catch (Exception exception) {
            if (process != null) {
                process.destroyForcibly();
            }
            throw new LogProcessingException("Could not run Python log processor", exception);
        }
    }

    private byte[] readOutput(Process process) {
        try (var input = process.getInputStream(); var output = new ByteArrayOutputStream()) {
            input.transferTo(output);
            return output.toByteArray();
        } catch (IOException exception) {
            throw new LogProcessingException("Could not read Python processor output", exception);
        }
    }

    public enum Platform {
        WINDOWS, LINUX
    }
}
