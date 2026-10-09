package com.inspection.logbackend;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class LogBackendApplication {

    public static void main(String[] args) {
        SpringApplication.run(LogBackendApplication.class, args);
    }

}
