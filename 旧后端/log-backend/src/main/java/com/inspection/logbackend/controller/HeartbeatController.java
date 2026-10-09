package com.inspection.logbackend.controller;

import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@RestController
@RequestMapping("/api/v1")
@Slf4j
public class HeartbeatController {

    /**
     * 核心存储：Key 为客户端计算机名 (client_id)，Value 为最后一次收到心跳的时间戳 (毫秒)
     * 使用 ConcurrentHashMap 保证多线程安全
     */
    public static final ConcurrentHashMap<String, Long> clientLastSeenMap = new ConcurrentHashMap<>();

    @PostMapping("/heartbeat")
    public ResponseEntity<String> handleHeartbeat(@RequestBody List<Map<String, Object>> payloadList) {

        // 因为是数组，我们需要遍历它（通常里面只有一条心跳数据）
        for (Map<String, Object> payload : payloadList) {
            // 提取 heartbeat.conf 中通过 modify 插件注入的 client_id
            String clientId = (String) payload.getOrDefault("client_id", "UNKNOWN_HOST");

            // 更新该机器在服务端的打卡时间
            clientLastSeenMap.put(clientId, System.currentTimeMillis());

            log.info("收到来自客户端 [{}] 的健康心跳...", clientId);
        }
        return ResponseEntity.ok("pong");
    }
}