package com.inspection.logbackend.controller;

import com.inspection.logbackend.service.LogIngestionService;
import com.inspection.logbackend.service.PythonLogProcessor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/logs")
@Slf4j
public class LogController {

    private final LogIngestionService logIngestionService;

    public LogController(LogIngestionService logIngestionService) {
        this.logIngestionService = logIngestionService;
    }

    @PostMapping("/windows")
    public ResponseEntity<Void> receiveWindowsLogs(@RequestBody String body) {
        return receiveLogs("Windows", PythonLogProcessor.Platform.WINDOWS, body);
    }

    @PostMapping("/linux")
    public ResponseEntity<Void> receiveLinuxLogs(@RequestBody String body) {
        return receiveLogs("Linux", PythonLogProcessor.Platform.LINUX, body);
    }

    private ResponseEntity<Void> receiveLogs(
            String hostType,
            PythonLogProcessor.Platform platform,
            String body) {
        int recordCount = logIngestionService.ingest(platform, body);
        log.info("Received and persisted {} logs: records={}, requestBytes={}",
                hostType, recordCount, body.getBytes(java.nio.charset.StandardCharsets.UTF_8).length);
        return ResponseEntity.ok().build();
    }

}
