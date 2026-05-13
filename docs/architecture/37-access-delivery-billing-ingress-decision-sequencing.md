# 37 — Access delivery vs billing ingress decision sequencing

### Status

**Proposed** — sequencing and hard-stop guidance only. This document does **not** replace ADR-32, 35, 36, or 33; it only records decision order and blocked work boundaries across them.

---

### A. Purpose

- Provide one source of truth for **sequencing** and **hard stops** across:
  - public billing ingress decisions in [31](31-public-billing-ingress-security.md) and [32](32-public-billing-ingress-decisions-adr.md);
  - Telegram safe delivery envelope in [35](35-user-facing-safe-access-delivery-envelope.md);
  - provider/storage policy in [36](36-access-config-provider-and-storage-policy.md);
  - deferred provider/storage/secure-channel questions in [33 §L](33-config-issuance-v1-design.md#l-open-questions-product--security--implementation-follow-up).
- Reduce ambiguity about what can proceed safely now versus what is blocked pending product/security/ops decisions.

---

### B. Context

- Current safe billing path is operator-only ingest/apply (no public listener required for the baseline).
- Public billing ingress remains blocked until ADR-32 section N/N2 gates are resolved and traceable in the decision record ([32](32-public-billing-ingress-decisions-adr.md)).
- Telegram resend exists as a feature-flagged, redacted flow; default remains explicit opt-in via `TELEGRAM_ACCESS_RESEND_ENABLE`.
- Safe delivery envelope [35](35-user-facing-safe-access-delivery-envelope.md) keeps `instruction` class disallowed for the current Telegram slice.
- Provider/storage policy [36](36-access-config-provider-and-storage-policy.md) defines criteria and checklist only; it does not choose vendor, SDK, schema, or production topology.

---

### C. Hard stops

The following implementation areas are blocked unless the corresponding decision gates are explicitly resolved.

1. **Public billing production listener**  
   Blocked until ADR-32 section N2 is complete and section B/G/L TBDs are resolved, or production public ingress is explicitly disabled by recorded decision ([32](32-public-billing-ingress-decisions-adr.md), [31](31-public-billing-ingress-security.md)).

2. **Telegram instruction/full-config delivery**  
   Blocked until [35](35-user-facing-safe-access-delivery-envelope.md) is explicitly revised and [36](36-access-config-provider-and-storage-policy.md) checklist decisions for channel/material controls are complete.

3. **Real provider adapter and storage schema**  
   Blocked until provider/storage decisions in [36](36-access-config-provider-and-storage-policy.md) and deferred follow-ups in [33 §L](33-config-issuance-v1-design.md#l-open-questions-product--security--implementation-follow-up) are resolved.

4. **Sensitive held material persistence**  
   Blocked until storage, key management, rotation, access control, retention, and legal approvals are recorded at policy level; no implicit approval is granted here.

5. **Retention destructive expansion**  
   Blocked until TTL/legal/product decisions are recorded in the relevant retention policy records.

---

### D. Recommended decision order

1. Decide provider/storage/security-channel policy scope in [36](36-access-config-provider-and-storage-policy.md) and [33 §L](33-config-issuance-v1-design.md#l-open-questions-product--security--implementation-follow-up), or explicitly record deferment.
2. Decide whether public billing ingress is needed; if yes, complete ADR-32 N2 gates in [32](32-public-billing-ingress-decisions-adr.md).
3. Decide user-facing delivery class/channel for `instruction`; if not approved, keep Telegram within redacted envelope in [35](35-user-facing-safe-access-delivery-envelope.md).
4. Only after the above, implement provider/webhook/storage slices with tests, redaction checks, and fail-closed controls.

---

### E. Allowed work while blocked

- Operator-only billing ingestion/apply and related bounded safety checks.
- Fake-provider and local smoke-style validation for existing non-public paths.
- Documentation and decision checklists that clarify policy boundaries.
- No-listener guards/tests that ensure runtime entrypoints do not expose public ingress.
- Redaction and contract tests for already shipped output surfaces.

### F. Disallowed work while blocked

- Public webhook/listener for billing ingress.
- Provider SDK integration with real credentials or production vendor assumptions.
- Storing raw delivery material or equivalent reconstructable secrets.
- Enabling instruction-class Telegram output.
- Flipping `TELEGRAM_ACCESS_RESEND_ENABLE` default enablement.
- Auto-apply directly from public ingress path.

---

### G. Ownership and sign-off roles

The following roles must be represented in sign-off for gates affecting their domain:

- **Product owner** — business intent, user-facing policy, rollout approvals.
- **Security owner** — threat model, data classification, channel/security controls.
- **Ops/on-call owner** — incident ownership, runbook readiness, operational viability.
- **Data/retention owner** — retention, legal hold, destructive-data policy.
- **Engineering owner** — architecture fit, implementation scope, test and rollout plan.

Roles are recorded as responsibilities, not as named individuals in this document.

---

### H. Compatibility with existing architecture docs

This sequencing note is compatible with and subordinate to:

- [31 — Public billing ingress security](31-public-billing-ingress-security.md)
- [32 — Public billing ingress decisions ADR](32-public-billing-ingress-decisions-adr.md)
- [33 — Config issuance v1 design](33-config-issuance-v1-design.md)
- [35 — User-facing safe access delivery envelope](35-user-facing-safe-access-delivery-envelope.md)
- [36 — Access/config provider and storage policy](36-access-config-provider-and-storage-policy.md)

---

### I. Non-goals

- No implementation work of any kind.
- No provider/vendor selection.
- No numeric replay/rate/body-limit constants.
- No schema, migration, or KMS design details.
- No user-facing copy/catalog updates.

---

### J. Future acceptance criteria (for implementation PRs)

Any future implementation PR in this area should:

- reference the relevant hard stop in this document and state whether it is resolved or intentionally out of scope;
- demonstrate tests that no secret/config material is output to user-facing surfaces;
- keep feature flags explicit opt-in until a recorded approval changes defaults.
