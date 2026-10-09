## Context

项目是一个新初始化的 Spring Boot 4.0.6 应用，当前：
- 引入了 `spring-boot-starter-webservices`（SOAP 起步依赖，实际不需要）
- 引入了 `mybatis-spring-boot-starter` + `mysql-connector-j`（为 Phase 2 持久化预留）
- 只有一个空的 `LogBackendApplication.java` 入口类
- 没有任何 Controller 或 HTTP 端点

Fluent Bit 会向 `POST /api/v1/logs` 发送 JSON 数组格式的 Windows Event Logs，要求后端接收并打印到控制台，返回 200 OK。

## Goals / Non-Goals

**Goals:**
- 提供 `POST /api/v1/logs` 端点接收 Fluent Bit 的 JSON 数组
- 使用 SLF4J Logger 将原始 JSON 打印到控制台
- 返回 HTTP 200 OK 给 Fluent Bit 作为确认
- 修正 pom.xml 依赖，使用正确的 Web 起步依赖

**Non-Goals:**
- 不解析/不提取 JSON 字段（原样打印）
- 不持久化到数据库（Phase 2）
- 不做鉴权/限流
- 不处理 Fluent Bit 重试逻辑

## Decisions

| 决策 | 选项 | 选择理由 |
|------|------|----------|
| 请求体类型 | `List<Map<String, Object>>` vs 具体 DTO | 用 `Map` 避免提前绑定字段结构，Fluent Bit 的 payload 字段可能变化 |
| Web 依赖 | `spring-boot-starter-web` vs 保留 `webservices` | `webservices` 是 SOAP/JAX-WS，与 REST 不匹配，必须替换 |
| 响应 | `200 OK` 空体 vs `{"status":"ok"}` | Fluent Bit http output 只关心状态码，空体减少流量 |
| 日志框架 | SLF4J/Logback（Spring Boot 默认） | 开箱即用，无需额外依赖 |

## Risks / Trade-offs

- **日志洪泛**: Windows Event Log 在高负载机器上可能产生大量日志 → 控制台输出可能被淹没。后续可考虑异步日志或采样
- **请求体大小**: 如果 Fluent Bit 批量发送大量记录，单个 POST 可能很大 → Spring Boot 默认 2MB，必要时增大 `spring.servlet.multipart.max-request-size` 或改用 Streaming
- **JSON 格式不匹配**: Fluent Bit 发送的不是标准 JSON 数组 → 反序列化失败时返回 400 Bad Request，不影响服务稳定性
