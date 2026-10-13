# Tests

- `unit/` — isolated domain rules and use cases.
- `integration/` — PostgreSQL/Redis adapters and transactional behaviour.
- `api/` — FastAPI contract and route behaviour.

Test module public contracts; avoid tests that require reaching into another module's private implementation.
