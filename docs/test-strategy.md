# Test strategy

Automated tests run with `pytest` and do not need Oracle or a model endpoint.

| Area | Test module | What it locks |
| --- | --- | --- |
| Startup and auth | `tests/test_smoke.py` | Health hides secrets, dashboard requires sign-in, controllers contain no SQL |
| Query catalog | `tests/test_sql_catalog.py` | Catalog statements are single `SELECT`s; the live gateway does not interpolate SQL |
| Diff and names | `tests/test_diff.py` | Line alignment, formatting-only normalization, identifier rejection |
| Signatures | `tests/test_signatures.py` | Optional suffix versus mandatory parameter with an external caller |
| Policy | `tests/test_recommendation.py` | Blocking and conditional outcomes |
| Regression | `tests/test_regression.py` | 22 change scenarios and their expected recommendation class |
| Browser slice and investigator | `tests/test_http_and_agent.py` | Both fixture reports, PDF and JSON export, CSRF, citation downgrade, budgets |

A regression scenario builds two fixture catalogs and runs `AnalysisService`. Expectations are recommendation class, finding category, evidence text, or the absence of a breaking flag. They do not assert a percentage.

The 22 scenarios are:

1. Mandatory parameter with an external caller
2. Backward-compatible optional parameter
3. Changed validation threshold
4. Changed tax calculation
5. Modified `UPDATE`, including a visible trigger name
6. New table absent from Production
7. New table present in Production
8. `COMMIT` moved ahead of a later DML statement
9. Swallowed `WHEN OTHERS`
10. Autonomous transaction
11. Dynamic SQL
12. Coordinated signature change across two selected packages
13. Inaccessible dependency metadata
14. Added overload
15. Removed routine
16. Unresolved synonym
17. Snapshot drift
18. Formatting-only edit
19. Removed parameter
20. Parameter type change
21. Parameter mode change
22. Removed exception handler

High-risk scenarios in this list are expected to finish as “not recommended” or “requires further investigation”. None of them finish as eligible for review.

Live Oracle tests are intentionally absent until an authorized nonproduction environment is provided. A passing fixture suite does not certify a real database.
