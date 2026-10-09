## Why

Fluent Bit 采集 Windows Event Logs 后需要通过 HTTP 转发到后端服务。当前项目是一个空壳 Spring Boot 应用，没有接收和处理日志的能力。需要搭建日志接收端点，让 Fluent Bit 的数据能落地，先打印到控制台，后续扩展存入 MySQL。

## What Changes

- 新增 REST 端点 `POST /api/v1/logs`，接收 Fluent Bit 发出的 JSON 数组格式日志
- 将 `spring-boot-starter-webservices` 替换为 `spring-boot-starter-web`（当前用的是 SOAP 起步依赖，与 REST 需求不匹配）
- 每次收到日志后，使用 Logger 将原始 JSON 打印到控制台
- 返回 `200 OK` 给 Fluent Bit 作为确认
- 保留 MyBatis + MySQL 依赖，为 Phase 2 持久化做准备

## Capabilities

### New Capabilities
- `log-ingestion`: 接收 Fluent Bit 通过 HTTP POST 发送的 Windows Event Logs，解析 JSON 数组，打印到控制台

### Modified Capabilities

<!-- 无现有 spec 需要修改 -->

## Impact

- **pom.xml**: `spring-boot-starter-webservices` → `spring-boot-starter-web`
- **新文件**: `LogController.java` (REST 端点)
- **application.properties**: 可能调整端口或日志配置
