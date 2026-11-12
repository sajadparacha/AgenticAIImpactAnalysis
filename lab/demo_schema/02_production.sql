-- Production (baseline) versions for the DEMO lab.
--
-- Run as the DEMO user in the Production environment (base edition), after
-- 01_shared.sql. If Test and Production are separate databases, run this file
-- only in Production.
--
-- These are the packages and synonyms callers use today. The Test versions are
-- in 03_test.sql. Nothing here is a report conclusion; the analysis engine
-- derives every finding from these dictionary objects.
--
-- Safe to re-run.

-- Production resolves QUOTE_BOOK to a local table. 03_test.sql replaces this
-- synonym with a Test-only version that points somewhere else.
CREATE OR REPLACE SYNONYM demo.quote_book FOR demo.quote_book_prod;
/

CREATE OR REPLACE PACKAGE demo.pkg_status AS
  FUNCTION get_employee_status(
    p_employee_id IN NUMBER
  ) RETURN VARCHAR2;
END pkg_status;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_status AS
  FUNCTION get_employee_status(
    p_employee_id IN NUMBER
  ) RETURN VARCHAR2 IS
    v_status VARCHAR2(50);
  BEGIN
    SELECT status
      INTO v_status
      FROM employee_status
     WHERE employee_id = p_employee_id;
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
    p_bill_date IN DATE
  );
END pkg_billing;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_billing AS
  PROCEDURE create_invoice(
    p_customer_id IN NUMBER,
    p_bill_date IN DATE
  ) IS
    v_total NUMBER := 0;
  BEGIN
    v_total := calculate_amount(p_customer_id, p_bill_date);
    INSERT INTO customer_bills (customer_id, bill_date, amount, status)
    VALUES (p_customer_id, p_bill_date, v_total, 'PENDING');
    COMMIT;
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
  PROCEDURE submit_bill(p_customer_id IN NUMBER, p_bill_date IN DATE) IS
  BEGIN
    pkg_billing.create_invoice(p_customer_id, p_bill_date);
  END submit_bill;
END pkg_legacy;
/

CREATE OR REPLACE PACKAGE demo.pkg_dispatch AS
  queue_full EXCEPTION;
  PROCEDURE send_batch(
    p_code IN VARCHAR2
  );
END pkg_dispatch;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_dispatch AS
  e_closed EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_closed, -20100);

  PROCEDURE send_batch(
    p_code IN VARCHAR2
  ) IS
    v_quote NUMBER;
  BEGIN
    SELECT quote
      INTO v_quote
      FROM quote_book
     WHERE code = p_code;
    IF v_quote IS NULL THEN
      RAISE_APPLICATION_ERROR(-20001, 'Quote is closed');
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

GRANT EXECUTE ON demo.pkg_dispatch TO demo_app;
/

-- Production (baseline) versions for PKG_ORDER, its caller, and PKG_LOOKUP.
-- The Test versions are in 03_test.sql.

CREATE OR REPLACE PACKAGE demo.pkg_order AS
  FUNCTION get_order_total(p_order_id IN NUMBER) RETURN NUMBER;
  PROCEDURE close_order(p_order_id IN NUMBER);
END pkg_order;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_order AS
  FUNCTION get_order_total(p_order_id IN NUMBER) RETURN NUMBER IS
    v_total NUMBER;
  BEGIN
    SELECT total
      INTO v_total
      FROM orders
     WHERE order_id = p_order_id;
    RETURN v_total;
  END;
  PROCEDURE close_order(p_order_id IN NUMBER) IS
  BEGIN
    UPDATE orders SET status = 'CLOSED' WHERE order_id = p_order_id;
    COMMIT;
  END;
END pkg_order;
/

CREATE OR REPLACE PACKAGE demo.pkg_order_caller AS
  PROCEDURE refresh_total(p_order_id IN NUMBER);
END pkg_order_caller;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_order_caller AS
  PROCEDURE refresh_total(p_order_id IN NUMBER) IS
    v_total NUMBER;
  BEGIN
    v_total := pkg_order.get_order_total(p_order_id);
  END refresh_total;
END pkg_order_caller;
/

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

GRANT EXECUTE ON demo.pkg_order TO demo_app;
/
GRANT EXECUTE ON demo.pkg_lookup TO demo_app;
/
