-- Test (proposed) versions for the DEMO lab.
--
-- Run as the DEMO user in the Test environment (the OIA_TEST edition when Test
-- and Production share one database), after 01_shared.sql and
-- 02_production.sql. If Test and Production are separate databases, run this
-- file only in Test, after 01_shared.sql.
--
-- Two fixture scenarios need an object that exists only in Test. Tables and
-- sequences cannot be editioned, so this lab represents them like this:
--   * BILLING_HISTORY is an editioned view over a shared backing table. It is
--     visible only in Test, so Production's dictionary has no object with that
--     name.
--   * A sequence cannot be editioned either, so the Test-only dependency is a
--     function (NEXT_DISPATCH_ID). On two genuinely separate databases you can
--     replace it with CREATE SEQUENCE demo.dispatch_seq and
--     v_id := dispatch_seq.NEXTVAL in the body below.
--
-- The PKG_LEGACY body is expected to fail with PLS-00306 in Test: it still
-- calls PKG_BILLING.CREATE_INVOICE with two arguments after the Test
-- specification added a mandatory third one. The object is created INVALID,
-- which is the state the analyzer reports.
--
-- Safe to re-run.

CREATE OR REPLACE VIEW demo.billing_history AS
SELECT customer_id, bill_date, amount
  FROM demo.billing_history_data;
/

CREATE OR REPLACE FUNCTION demo.next_batch(p_code IN VARCHAR2) RETURN NUMBER IS
BEGIN
  RETURN 1;
END next_batch;
/

CREATE OR REPLACE FUNCTION demo.next_dispatch_id RETURN NUMBER IS
BEGIN
  RETURN 1;
END next_dispatch_id;
/

CREATE OR REPLACE SYNONYM demo.quote_book FOR demo.quote_book_local;
/

CREATE OR REPLACE PACKAGE demo.pkg_status AS
  FUNCTION get_employee_status(
    p_employee_id IN NUMBER,
    p_include_history IN BOOLEAN DEFAULT FALSE
  ) RETURN VARCHAR2;
END pkg_status;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_status AS
  FUNCTION get_employee_status(
    p_employee_id IN NUMBER,
    p_include_history IN BOOLEAN DEFAULT FALSE
  ) RETURN VARCHAR2 IS
    v_status VARCHAR2(50);
  BEGIN
    SELECT status
      INTO v_status
      FROM employee_status
     WHERE employee_id = p_employee_id;
    IF p_include_history THEN
      v_status := v_status || ' (history)';
    END IF;
    RETURN v_status;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RETURN 'NOT_FOUND';
  END get_employee_status;
END pkg_status;
/

CREATE OR REPLACE PACKAGE demo.pkg_billing AS
  PROCEDURE create_invoice(
    p_customer_id IN NUMBER,
    p_bill_date IN DATE,
    p_include_discounts IN BOOLEAN,
    p_force_recalc IN BOOLEAN DEFAULT FALSE
  );
END pkg_billing;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_billing AS
  PROCEDURE create_invoice(
    p_customer_id IN NUMBER,
    p_bill_date IN DATE,
    p_include_discounts IN BOOLEAN,
    p_force_recalc IN BOOLEAN DEFAULT FALSE
  ) IS
    v_total NUMBER := 0;
    v_tax NUMBER := 0;
  BEGIN
    v_total := calculate_amount(p_customer_id, p_bill_date);
    v_tax := v_total * 0.15;
    v_total := v_total + v_tax;
    INSERT INTO customer_bills (customer_id, bill_date, amount, status)
    VALUES (p_customer_id, p_bill_date, v_total, 'PENDING');
    COMMIT;
    INSERT INTO demo.billing_history (customer_id, bill_date, amount)
    VALUES (p_customer_id, p_bill_date, v_total);
  END create_invoice;
END pkg_billing;
/

CREATE OR REPLACE PACKAGE demo.pkg_dashboard AS
  PROCEDURE show_status(p_employee_id IN NUMBER);
END pkg_dashboard;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_dashboard AS
  PROCEDURE show_status(p_employee_id IN NUMBER) IS
    v_status VARCHAR2(50);
  BEGIN
    v_status := pkg_status.get_employee_status(p_employee_id);
  END show_status;
END pkg_dashboard;
/

CREATE OR REPLACE PACKAGE demo.pkg_legacy AS
  PROCEDURE submit_bill(p_customer_id IN NUMBER, p_bill_date IN DATE);
END pkg_legacy;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_legacy AS
  -- Test version of the unchanged caller. The comment makes this a distinct
  -- editioned source, so Oracle recompiles it against the Test interface.
  PROCEDURE submit_bill(p_customer_id IN NUMBER, p_bill_date IN DATE) IS
  BEGIN
    pkg_billing.create_invoice(p_customer_id, p_bill_date);
  END submit_bill;
END pkg_legacy;
/

CREATE OR REPLACE PACKAGE demo.pkg_dispatch AUTHID CURRENT_USER AS
  batch_rejected EXCEPTION;
  PROCEDURE send_batch(
    p_code IN VARCHAR2
  );
END pkg_dispatch;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_dispatch AS
  e_closed EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_closed, -20110);

  PROCEDURE send_batch(
    p_code IN VARCHAR2
  ) IS
    v_quote NUMBER;
    v_id NUMBER;
    v_batch NUMBER;
    v_sql VARCHAR2(200);
  BEGIN
    SELECT quote
      INTO v_quote
      FROM quote_book
     WHERE code = p_code;
    v_id := next_dispatch_id();
    v_batch := next_batch(p_code);
    v_sql := 'UPDATE demo.outbox SET state = :1 WHERE id = :2';
    EXECUTE IMMEDIATE v_sql USING 'SENT', v_id;
    IF v_quote IS NULL THEN
      RAISE_APPLICATION_ERROR(-20041, 'Batch was rejected');
    END IF;
  EXCEPTION
    WHEN e_closed THEN
      RAISE;
  END send_batch;
END pkg_dispatch;
/

CREATE OR REPLACE PACKAGE demo.pkg_mailer AS
  PROCEDURE deliver(p_code IN VARCHAR2);
END pkg_mailer;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_mailer AS
  PROCEDURE deliver(p_code IN VARCHAR2) IS
  BEGIN
    pkg_dispatch.send_batch(p_code);
  END deliver;
END pkg_mailer;
/

-- Both environments grant DEMO_APP. On one database the Test edition inherits
-- it from 02; on a separate Test database it has to be granted here.
GRANT EXECUTE ON demo.pkg_dispatch TO demo_app;
/

-- A grant that exists only in Test. Object privileges are editioned together
-- with the object, so Production keeps only the DEMO_APP grant from 02.
GRANT EXECUTE ON demo.pkg_dispatch TO demo_dispatcher;
/

-- PKG_ORDER test version: AUTHID CURRENT_USER, a public exception, a mandatory
-- parameter, a Test-only view (ORDER_AUDIT over the shared ORDER_AUDIT_DATA
-- table) and function (ORDER_FEE), a commit before a later insert, dynamic SQL,
-- and a grant Production does not have.

CREATE OR REPLACE VIEW demo.order_audit AS
SELECT order_id, note
  FROM demo.order_audit_data;
/

CREATE OR REPLACE FUNCTION demo.order_fee(p_order_id IN NUMBER) RETURN NUMBER IS
BEGIN
  RETURN 15;
END order_fee;
/

CREATE OR REPLACE PACKAGE demo.pkg_order AUTHID CURRENT_USER AS
  order_closed EXCEPTION;
  FUNCTION get_order_total(p_order_id IN NUMBER, p_include_tax IN BOOLEAN) RETURN NUMBER;
  PROCEDURE close_order(p_order_id IN NUMBER);
END pkg_order;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_order AS
  FUNCTION get_order_total(p_order_id IN NUMBER, p_include_tax IN BOOLEAN) RETURN NUMBER IS
    v_total NUMBER;
  BEGIN
    SELECT total
      INTO v_total
      FROM orders
     WHERE order_id = p_order_id;
    IF p_include_tax THEN
      v_total := v_total + order_fee(p_order_id);
    END IF;
    RETURN v_total;
  END;
  PROCEDURE close_order(p_order_id IN NUMBER) IS
    v_sql VARCHAR2(200);
  BEGIN
    UPDATE orders SET status = 'CLOSED' WHERE order_id = p_order_id;
    COMMIT;
    INSERT INTO order_audit (order_id, note) VALUES (p_order_id, 'closed');
    v_sql := 'UPDATE outbox SET state = :1 WHERE id = :2';
    EXECUTE IMMEDIATE v_sql USING 'SENT', p_order_id;
  END;
END pkg_order;
/

-- The unchanged caller. Its one-argument call to GET_ORDER_TOTAL no longer
-- compiles against the Test specification, so the body is created INVALID.
-- The comment makes this a distinct editioned source, so Oracle recompiles it
-- against the Test interface instead of reusing the Production body.
CREATE OR REPLACE PACKAGE demo.pkg_order_caller AS
  PROCEDURE refresh_total(p_order_id IN NUMBER);
END pkg_order_caller;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_order_caller AS
  -- Test version of the unchanged caller. The comment makes this a distinct
  -- editioned source, so Oracle recompiles it against the Test interface.
  PROCEDURE refresh_total(p_order_id IN NUMBER) IS
    v_total NUMBER;
  BEGIN
    v_total := pkg_order.get_order_total(p_order_id);
  END refresh_total;
END pkg_order_caller;
/

-- PKG_LOOKUP is unchanged between Production and Test.
CREATE OR REPLACE PACKAGE demo.pkg_lookup AS
  FUNCTION find_region(p_customer_id IN NUMBER) RETURN VARCHAR2;
END pkg_lookup;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_lookup AS
  FUNCTION find_region(p_customer_id IN NUMBER) RETURN VARCHAR2 IS
    v_region VARCHAR2(20);
  BEGIN
    SELECT region
      INTO v_region
      FROM customers
     WHERE customer_id = p_customer_id;
    RETURN v_region;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RETURN 'UNKNOWN';
  END find_region;
END pkg_lookup;
/

-- Both environments grant DEMO_APP. On a separate Test database this grant has
-- to be applied here; on one database the Test edition inherits it from 02.
GRANT EXECUTE ON demo.pkg_order TO demo_app;
/
-- A grant that exists only in Test.
GRANT EXECUTE ON demo.pkg_order TO demo_order_svc;
/
GRANT EXECUTE ON demo.pkg_lookup TO demo_app;
/
