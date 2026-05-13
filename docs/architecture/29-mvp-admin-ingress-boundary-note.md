## 29 - MVP admin ingress boundary note

### Decision

For MVP, the only admin ingress is `internal admin endpoint`.

`Telegram admin chat` is deferred and explicitly not part of MVP.

### Decision drivers

- clearer security boundary for privileged operations
- structured validation at ingress before application handling
- easier mandatory audit for all state-changing admin actions
- lower ambiguity than chat-command ingress
- easier separation from user-facing bot flows

### Security guardrails

- RBAC or strict admin allowlist is required for every admin action
- mandatory audit is required for every state-changing admin action
- strict input validation is required on admin ingress
- logs must keep minimal PII and avoid sensitive payload details

### Non-goals / out of scope

- no implementation details for transport, handlers, or storage
- no rollout plan, migration plan, or operational runbook
- no billing/subscription/config issuance scope expansion
- no retry/backoff/httpx scope
- no simultaneous dual-ingress MVP

### Deferred alternative

`Telegram admin chat` is deferred because chat ingress has higher ambiguity in command intent and a weaker default boundary for privileged actions.

It may be revisited after MVP as a separate boundary decision with dedicated controls and explicit risk acceptance.

**Related:** Standalone ADM-01 internal HTTP entrypoint exists (`python -m app.internal_admin`) but is disabled by default; production exposure remains governed by [34 - ADM-01 internal HTTP production boundary ADR](34-adm01-internal-http-production-boundary-adr.md), including private-network/trusted-proxy/mTLS and allowlist constraints.
