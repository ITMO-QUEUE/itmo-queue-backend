# Queues module

Live queue and slot-based mode, commands, state machine, and results.

- `domain/` — framework-independent model and business rules.
- `application/` — use cases / orchestration.
- `ports/` — interfaces required by this module.

Other modules must depend only on the documented public application contract, not on private implementation details.