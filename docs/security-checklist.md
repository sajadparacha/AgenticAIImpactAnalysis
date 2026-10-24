# Security checklist

- [x] Separate Test and Production settings. Passwords come from the environment, not source code.
- [x] Fixture mode defaults on so a missing configuration cannot fall through to Production.
- [x] Live SQL is a fixed `SELECT` catalog plus `SET TRANSACTION READ ONLY`.
- [x] Bind variables carry owners, names, and types. Identifiers are allowlisted before use.
- [x] The investigator cannot pass SQL. Tool names and arguments are allowlisted, and objects must already be in the run.
- [x] Egress is off unless `OIA_LLM_EGRESS_ENABLED` is set. The adapter raises before any HTTP call when it is off.
- [x] Tool logs store names and byte sizes, not source, DSNs, or tokens.
- [x] Health output omits DSNs, users, passwords, and API keys.
- [x] Run ids are UUIDs and cannot address a path outside `runs/`.
- [x] Analysis form uses a session-bound CSRF token. The session cookie is `httpOnly` and `SameSite=Lax`.
- [x] Security headers: `nosniff`, `DENY` framing, no-referrer, no-store, and a self-only content security policy.
- [x] Default bind address is `127.0.0.1`.
- [ ] Replace the shared development token with SSO or a reverse-proxy identity before any non-local deployment.
- [ ] Set `OIA_COOKIE_SECURE=true` behind HTTPS.
- [ ] Use distinct read-only Oracle accounts. Do not grant `DBA` views or execute rights.
- [ ] Approve any external model explicitly before enabling egress.
- [ ] Confirm `runs/` permissions so snapshots are not world-readable.

No production write path was added. The catalog test fails if a query stops being a single `SELECT`.
