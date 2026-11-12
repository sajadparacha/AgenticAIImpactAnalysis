-- Synthetic data for exercising the DEMO lab packages by hand.
--
-- Run as the DEMO user after 01_shared.sql. On one database the tables are not
-- editioned, so one run fills them for both Test and Production. If Test and
-- Production are separate databases, run it in both.
--
-- Re-running resets the six tables to exactly this data, including rows the
-- packages inserted or updated while you were testing them.
--
-- The rows are chosen so each package shows a visible difference between the
-- Production (ORA$BASE) and Test (OIA_TEST) versions:
--
--   PKG_STATUS.GET_EMPLOYEE_STATUS
--     Employees 1-10 cover every status, 7 has a NULL status, and 999 does not
--     exist (returns NOT_FOUND).
--
--   PKG_BILLING.CREATE_INVOICE
--     Customers 1001-1005 already have bills for September and October 2026.
--     Free dates: any customer on 2026-11-01, except the trap below.
--     (1001, 2026-10-01) already exists: ORA-00001 in both versions.
--     (1002, 2026-11-01) exists only in BILLING_HISTORY_DATA. The Test version
--     commits the bill, then fails on the history insert, so the bill stays.
--     That is the partial-commit hazard the analyzer reports.
--
--   PKG_DISPATCH.SEND_BATCH (QUOTE_BOOK resolves differently per edition)
--     ABC        both tables, same quote
--     XYZ        both tables, quote 40 in Production and 42 in Test
--     CLOSED     NULL quote: Production raises -20001, Test raises -20041
--     PROD_ONLY  only in QUOTE_BOOK_PROD: ORA-01403 in Test
--     TEST_ONLY  only in QUOTE_BOOK_LOCAL: ORA-01403 in Production
--     The Test version also sets OUTBOX row 1 (NEXT_DISPATCH_ID) to SENT.

BEGIN
  DELETE FROM demo.employee_status;
  DELETE FROM demo.customer_bills;
  DELETE FROM demo.billing_history_data;
  DELETE FROM demo.outbox;
  DELETE FROM demo.quote_book_prod;
  DELETE FROM demo.quote_book_local;
  DELETE FROM demo.orders;
  DELETE FROM demo.order_audit_data;
  DELETE FROM demo.customers;

  INSERT INTO demo.employee_status (employee_id, status) VALUES (1, 'ACTIVE');
  INSERT INTO demo.employee_status (employee_id, status) VALUES (2, 'ACTIVE');
  INSERT INTO demo.employee_status (employee_id, status) VALUES (3, 'ON_LEAVE');
  INSERT INTO demo.employee_status (employee_id, status) VALUES (4, 'SUSPENDED');
  INSERT INTO demo.employee_status (employee_id, status) VALUES (5, 'TERMINATED');
  INSERT INTO demo.employee_status (employee_id, status) VALUES (6, 'PROBATION');
  INSERT INTO demo.employee_status (employee_id, status) VALUES (7, NULL);
  INSERT INTO demo.employee_status (employee_id, status) VALUES (8, 'ACTIVE');
  INSERT INTO demo.employee_status (employee_id, status) VALUES (9, 'CONTRACTOR');
  INSERT INTO demo.employee_status (employee_id, status) VALUES (10, 'RETIRED');

  INSERT INTO demo.customer_bills (customer_id, bill_date, amount, status) VALUES (1001, DATE '2026-09-01', 100, 'PAID');
  INSERT INTO demo.customer_bills (customer_id, bill_date, amount, status) VALUES (1001, DATE '2026-10-01', 115, 'PENDING');
  INSERT INTO demo.customer_bills (customer_id, bill_date, amount, status) VALUES (1002, DATE '2026-09-01', 100, 'PAID');
  INSERT INTO demo.customer_bills (customer_id, bill_date, amount, status) VALUES (1002, DATE '2026-10-01', 100, 'OVERDUE');
  INSERT INTO demo.customer_bills (customer_id, bill_date, amount, status) VALUES (1003, DATE '2026-09-01', 250, 'PAID');
  INSERT INTO demo.customer_bills (customer_id, bill_date, amount, status) VALUES (1003, DATE '2026-10-01', 250, 'CANCELLED');
  INSERT INTO demo.customer_bills (customer_id, bill_date, amount, status) VALUES (1004, DATE '2026-10-01', 80, 'PENDING');
  INSERT INTO demo.customer_bills (customer_id, bill_date, amount, status) VALUES (1005, DATE '2026-10-01', 0, 'PAID');

  INSERT INTO demo.billing_history_data (customer_id, bill_date, amount) VALUES (1001, DATE '2026-08-01', 100);
  INSERT INTO demo.billing_history_data (customer_id, bill_date, amount) VALUES (1001, DATE '2026-09-01', 100);
  INSERT INTO demo.billing_history_data (customer_id, bill_date, amount) VALUES (1002, DATE '2026-09-01', 100);
  INSERT INTO demo.billing_history_data (customer_id, bill_date, amount) VALUES (1003, DATE '2026-09-01', 250);
  INSERT INTO demo.billing_history_data (customer_id, bill_date, amount) VALUES (1002, DATE '2026-11-01', 115);

  INSERT INTO demo.outbox (id, state) VALUES (1, 'PENDING');
  INSERT INTO demo.outbox (id, state) VALUES (2, 'PENDING');
  INSERT INTO demo.outbox (id, state) VALUES (3, 'SENT');
  INSERT INTO demo.outbox (id, state) VALUES (4, 'FAILED');
  INSERT INTO demo.outbox (id, state) VALUES (5, 'CANCELLED');

  INSERT INTO demo.quote_book_prod (code, quote) VALUES ('ABC', 12.5);
  INSERT INTO demo.quote_book_prod (code, quote) VALUES ('XYZ', 40);
  INSERT INTO demo.quote_book_prod (code, quote) VALUES ('CLOSED', NULL);
  INSERT INTO demo.quote_book_prod (code, quote) VALUES ('PROD_ONLY', 7);

  INSERT INTO demo.quote_book_local (code, quote) VALUES ('ABC', 12.5);
  INSERT INTO demo.quote_book_local (code, quote) VALUES ('XYZ', 42);
  INSERT INTO demo.quote_book_local (code, quote) VALUES ('CLOSED', NULL);
  INSERT INTO demo.quote_book_local (code, quote) VALUES ('TEST_ONLY', 9);

  INSERT INTO demo.orders (order_id, total, status) VALUES (5001, 100, 'OPEN');
  INSERT INTO demo.orders (order_id, total, status) VALUES (5002, 250, 'OPEN');
  INSERT INTO demo.orders (order_id, total, status) VALUES (5003, 0, 'CLOSED');

  INSERT INTO demo.customers (customer_id, region) VALUES (7001, 'NORTH');
  INSERT INTO demo.customers (customer_id, region) VALUES (7002, 'SOUTH');
  INSERT INTO demo.customers (customer_id, region) VALUES (7003, NULL);

  COMMIT;
END;
/
