### Requirement: Accept log POST requests
The system SHALL accept HTTP POST requests to `/api/v1/logs` from Fluent Bit.

#### Scenario: Receive valid log batch
- **WHEN** Fluent Bit sends a POST request to `/api/v1/logs` with a JSON array body and `Content-Type: application/json`
- **THEN** the system SHALL return HTTP 200 OK

#### Scenario: Receive malformed JSON
- **WHEN** Fluent Bit sends a POST request with invalid JSON body
- **THEN** the system SHALL return HTTP 400 Bad Request

### Requirement: Print logs to console
The system SHALL print received log records to the console using SLF4J Logger at INFO level.

#### Scenario: Log printed for each batch
- **WHEN** a valid log batch is received
- **THEN** the system SHALL output the raw JSON array to the console via Logger.info()

### Requirement: Accept JSON array format
The system SHALL accept a JSON array as the request body, where each element is a JSON object representing a log record.

#### Scenario: Array with multiple records
- **WHEN** the request body is `[{"EventID": 4624}, {"EventID": 1001}]`
- **THEN** the system SHALL process all records in the array
