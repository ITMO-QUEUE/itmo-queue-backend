# Integrations / Itmoid module

ITMO.ID provider adapter and identity synchronization boundary.

- `domain/` — framework-independent model and business rules.
- `application/` — use cases / orchestration.
- `ports/` — interfaces required by this module.

Other modules must depend only on the documented public application contract, not on private implementation details.