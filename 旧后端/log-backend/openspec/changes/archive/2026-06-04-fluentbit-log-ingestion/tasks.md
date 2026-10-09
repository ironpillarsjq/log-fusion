## 1. 依赖修正

- [x] 1.1 将 pom.xml 中的 `spring-boot-starter-webservices` 替换为 `spring-boot-starter-web`

## 2. 核心实现
it
- [x] 2.1 新建 `LogController.java`，添加 `@PostMapping("/api/v1/logs")` 端点
- [x] 2.2 端点接收 `@RequestBody List<Map<String, Object>>` 参数
- [x] 2.3 使用 SLF4J Logger 的 `info()` 方法将接收到的 JSON 数组打印到控制台
- [x] 2.4 返回 `ResponseEntity.ok().build()` 给 Fluent B

## 3. 验证

- [ ] 3.1 启动应用，用 curl 或 Postman 发送测试请求验证端点可用
- [ ] 3.2 验证非法 JSON 返回 400 Bad Request
