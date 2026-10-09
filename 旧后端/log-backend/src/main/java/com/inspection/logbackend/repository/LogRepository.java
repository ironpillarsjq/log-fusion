package com.inspection.logbackend.repository;

import com.inspection.logbackend.service.LogProcessingException;
import com.inspection.logbackend.service.PythonLogProcessor.Platform;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.BatchPreparedStatementSetter;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import tools.jackson.databind.ObjectMapper;

import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.sql.Types;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

@Repository
public class LogRepository {

    private static final Map<String, String> WINDOWS_TABLES = Map.of(
            "application", "application_logs",
            "security", "security_logs",
            "setup", "setup_logs",
            "system", "system_logs",
            "forwardedevents", "forwardedevents_logs");

    private static final Map<String, List<String>> LINUX_COLUMNS = Map.of(
            "authentication_session", List.of(
                    "event_time", "event_id", "category", "type", "pid", "uid", "auid", "session_id",
                    "operation", "account", "executable", "hostname", "source_address", "terminal", "result", "tips"),
            "account_security_change", List.of(
                    "event_time", "event_id", "category", "type", "pid", "uid", "auid", "session_id",
                    "operation", "executable", "hostname", "source_address", "terminal", "result", "tips"),
            "process_command_execution", List.of(
                    "event_time", "event_id", "category", "type", "pid", "ppid", "uid", "auid", "session_id",
                    "executable", "command", "result", "tips"),
            "file_object_access", List.of(
                    "event_time", "event_id", "category", "type", "pid", "ppid", "uid", "auid", "session_id",
                    "executable", "command", "object_path", "object_inode", "action", "result", "tips"),
            "network_ipc_communication", List.of(
                    "event_time", "event_id", "category", "type", "pid", "ppid", "uid", "auid", "session_id",
                    "executable", "command", "action", "source_address", "destination_address", "source_port",
                    "destination_port", "protocol", "result", "tips"),
            "security_policy_config_change", List.of(
                    "event_time", "event_id", "category", "type", "pid", "uid", "auid", "session_id",
                    "action", "target", "executable", "result", "tips"),
            "system_service_audit_lifecycle", List.of(
                    "event_time", "event_id", "category", "type", "pid", "uid", "auid", "session_id",
                    "action", "target", "executable", "hostname", "result", "tips"));

    private final JdbcTemplate jdbcTemplate;
    private final ObjectMapper objectMapper;
    private final String windowsDatabase;
    private final String linuxDatabase;

    public LogRepository(
            JdbcTemplate jdbcTemplate,
            ObjectMapper objectMapper,
            @Value("${log-database.windows:windows_logs}") String windowsDatabase,
            @Value("${log-database.linux:log-linux}") String linuxDatabase) {
        this.jdbcTemplate = jdbcTemplate;
        this.objectMapper = objectMapper;
        this.windowsDatabase = windowsDatabase;
        this.linuxDatabase = linuxDatabase;
    }

    public int save(Platform platform, List<Map<String, Object>> records) {
        if (records.isEmpty()) {
            return 0;
        }
        return platform == Platform.WINDOWS ? saveWindows(records) : saveLinux(records);
    }

    private int saveWindows(List<Map<String, Object>> records) {
        Map<String, List<Map<String, Object>>> grouped = new LinkedHashMap<>();
        for (Map<String, Object> record : records) {
            String table = windowsTable(record.get("channel"));
            grouped.computeIfAbsent(table, ignored -> new ArrayList<>()).add(record);
        }

        int saved = 0;
        for (Map.Entry<String, List<Map<String, Object>>> entry : grouped.entrySet()) {
            List<Map<String, Object>> batch = entry.getValue();
            jdbcTemplate.batchUpdate(windowsInsertSql(entry.getKey()), windowsSetter(batch));
            saved += batch.size();
        }
        return saved;
    }

    private int saveLinux(List<Map<String, Object>> records) {
        Map<String, List<Map<String, Object>>> grouped = new LinkedHashMap<>();
        for (Map<String, Object> record : records) {
            String category = value(record, "category");
            if (!LINUX_COLUMNS.containsKey(category)) {
                throw new LogProcessingException("Unsupported Linux log category: " + category);
            }
            grouped.computeIfAbsent(category, ignored -> new ArrayList<>()).add(record);
        }

        int saved = 0;
        for (Map.Entry<String, List<Map<String, Object>>> entry : grouped.entrySet()) {
            List<String> columns = LINUX_COLUMNS.get(entry.getKey());
            List<Map<String, Object>> batch = entry.getValue();
            jdbcTemplate.batchUpdate(linuxInsertSql(entry.getKey(), columns), linuxSetter(batch, columns));
            saved += batch.size();
        }
        return saved;
    }

    private String windowsTable(Object channelValue) {
        String channel = channelValue == null ? "application" : String.valueOf(channelValue).toLowerCase(Locale.ROOT);
        String normalized = channel.replace("-", "").replace("_", "");
        return WINDOWS_TABLES.getOrDefault(normalized, "application_logs");
    }

    private String windowsInsertSql(String table) {
        return "INSERT INTO " + qualified(windowsDatabase, table) + " " +
                "(`source_year`, `source_file`, `provider_name`, `provider_guid`, `event_id`, `event_version`, " +
                "`level`, `task`, `opcode`, `keywords`, `time_created`, `event_record_id`, " +
                "`correlation_activity_id`, `correlation_related_activity_id`, `execution_process_id`, " +
                "`execution_thread_id`, `channel`, `computer`, `security_user_id`, `system_extra`, `eventdata`) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)";
    }

    private BatchPreparedStatementSetter windowsSetter(List<Map<String, Object>> records) {
        return new BatchPreparedStatementSetter() {
            @Override
            public void setValues(PreparedStatement ps, int index) throws SQLException {
                Map<String, Object> record = records.get(index);
                setLong(ps, 1, year(record.get("event_time")));
                String client = value(record, "client_id");
                String channel = value(record, "channel");
                setString(ps, 2, "fluent-bit://" + (client == null ? "unknown" : client) + "/" +
                        (channel == null ? "unknown" : channel));
                setString(ps, 3, record.get("source_name"));
                setString(ps, 4, null);
                setString(ps, 5, record.get("event_id"));
                setString(ps, 6, null);
                setString(ps, 7, record.get("event_type"));
                setString(ps, 8, null);
                setString(ps, 9, null);
                setString(ps, 10, null);
                setTimestamp(ps, 11, record.get("event_time"));
                setLong(ps, 12, record.get("record_number"));
                setString(ps, 13, null);
                setString(ps, 14, null);
                setString(ps, 15, null);
                setString(ps, 16, null);
                setString(ps, 17, channel);
                setString(ps, 18, record.get("computer_name"));
                setString(ps, 19, null);
                setString(ps, 20, null);
                setJson(ps, 21, record.get("raw"));
            }

            @Override
            public int getBatchSize() {
                return records.size();
            }
        };
    }

    private String linuxInsertSql(String table, List<String> columns) {
        String columnSql = columns.stream().map(column -> "`" + column + "`").reduce((a, b) -> a + ", " + b).orElseThrow();
        String values = String.join(", ", Collections.nCopies(columns.size(), "?"));
        return "INSERT INTO " + qualified(linuxDatabase, table) + " (" + columnSql + ") VALUES (" + values + ")";
    }

    private BatchPreparedStatementSetter linuxSetter(List<Map<String, Object>> records, List<String> columns) {
        return new BatchPreparedStatementSetter() {
            @Override
            public void setValues(PreparedStatement ps, int index) throws SQLException {
                Map<String, Object> normalized = normalized(records.get(index));
                for (int i = 0; i < columns.size(); i++) {
                    setField(ps, i + 1, columns.get(i), normalized);
                }
            }

            @Override
            public int getBatchSize() {
                return records.size();
            }
        };
    }

    private Map<String, Object> normalized(Map<String, Object> record) {
        Object value = record.get("normalized");
        if (value instanceof Map<?, ?> map) {
            Map<String, Object> result = new LinkedHashMap<>();
            map.forEach((key, item) -> result.put(String.valueOf(key), item));
            return result;
        }
        return record;
    }

    private void setField(PreparedStatement ps, int index, String field, Map<String, Object> record) throws SQLException {
        Object value = record.get(field);
        if (field.equals("event_time")) {
            setTimestamp(ps, index, value);
        } else if (field.equals("event_id") || field.equals("pid") || field.equals("ppid") ||
                field.equals("uid") || field.equals("auid") || field.equals("session_id") ||
                field.equals("object_inode") || field.equals("source_port") || field.equals("destination_port")) {
            setLong(ps, index, value);
        } else {
            setString(ps, index, value);
        }
    }

    private String qualified(String database, String table) {
        return "`" + database.replace("`", "``") + "`.`" + table.replace("`", "``") + "`";
    }

    private String value(Map<String, Object> record, String field) {
        Object value = record.get(field);
        return value == null || String.valueOf(value).isBlank() ? null : String.valueOf(value);
    }

    private Long year(Object value) {
        String text = value == null ? null : String.valueOf(value);
        if (text == null || text.length() < 4) {
            return null;
        }
        try {
            return Long.parseLong(text.substring(0, 4));
        } catch (NumberFormatException ignored) {
            return null;
        }
    }

    private void setString(PreparedStatement ps, int index, Object value) throws SQLException {
        if (value == null) {
            ps.setNull(index, Types.VARCHAR);
        } else {
            ps.setString(index, String.valueOf(value));
        }
    }

    private void setJson(PreparedStatement ps, int index, Object value) throws SQLException {
        if (value == null) {
            ps.setString(index, "{}");
        } else if (value instanceof Map<?, ?> || value instanceof List<?>) {
            ps.setString(index, objectMapper.writeValueAsString(value));
        } else {
            ps.setString(index, String.valueOf(value));
        }
    }

    private void setLong(PreparedStatement ps, int index, Object value) throws SQLException {
        if (value == null || String.valueOf(value).isBlank()) {
            ps.setNull(index, Types.BIGINT);
        } else {
            try {
                ps.setLong(index, Long.parseLong(String.valueOf(value)));
            } catch (NumberFormatException exception) {
                ps.setNull(index, Types.BIGINT);
            }
        }
    }

    private void setTimestamp(PreparedStatement ps, int index, Object value) throws SQLException {
        if (value == null || String.valueOf(value).isBlank()) {
            ps.setNull(index, Types.TIMESTAMP);
            return;
        }
        String text = String.valueOf(value);
        try {
            ps.setObject(index, LocalDateTime.ofInstant(Instant.parse(text), ZoneOffset.UTC));
        } catch (RuntimeException ignored) {
            try {
                ps.setObject(index, OffsetDateTime.parse(text).toInstant().atOffset(ZoneOffset.UTC).toLocalDateTime());
            } catch (RuntimeException exception) {
                ps.setNull(index, Types.TIMESTAMP);
            }
        }
    }
}
