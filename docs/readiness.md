# Release readiness

Version 1 is ready for local review and fixture demonstrations. It is not ready to be treated as a production promotion authority.

## Ready

- A signed-in user can submit one or more packages and open a computed report.
- Low-risk and high-risk fixture packages produce different recommendations.
- Findings carry object identity, line or metadata evidence, severity, qualitative confidence, and status.
- HTML, PDF, and JSON exports are generated from the report model.
- The automated suite passes without Oracle and without an API key.
- The investigator is bounded, off by default, and unable to confirm unsupported claims.

## Not ready

- No authorized Oracle environment was connected during this implementation. Dictionary grants, thin-mode compatibility, and snapshot timing on a real database are unverified.
- Enterprise authentication is not implemented.
- The scanner has the limitations in `docs/known-limitations.md`. A human still has to read high-impact financial and transaction changes.
- A positive recommendation is an input to review, not an approval to promote.

## Before a pilot on real packages

1. Create read-only Test and Production users with the dictionary grants in `docs/architecture.md`.
2. Set `OIA_FIXTURE_MODE=false` and confirm a single known package snapshots from both sides.
3. Keep LLM egress off until an approved endpoint is named.
4. Put the application behind the organization’s identity provider and TLS.
5. Compare the report for that package with a manual review before anyone uses it in a promotion meeting.
