package com.inspection.logbackend.service;

import com.inspection.logbackend.repository.LogRepository;
import org.springframework.stereotype.Service;

@Service
public class LogIngestionService {

    private final RawLogFileWriter rawLogFileWriter;
    private final PythonLogProcessor pythonLogProcessor;
    private final LogRepository logRepository;

    public LogIngestionService(
            RawLogFileWriter rawLogFileWriter,
            PythonLogProcessor pythonLogProcessor,
            LogRepository logRepository) {
        this.rawLogFileWriter = rawLogFileWriter;
        this.pythonLogProcessor = pythonLogProcessor;
        this.logRepository = logRepository;
    }

    public int ingest(PythonLogProcessor.Platform platform, String rawBody) {
        if (platform == PythonLogProcessor.Platform.WINDOWS) {
            rawLogFileWriter.appendWindows(rawBody);
        } else {
            rawLogFileWriter.appendLinux(rawBody);
        }
        return logRepository.save(platform, pythonLogProcessor.process(platform, rawBody));
    }
}
