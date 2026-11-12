-- Case packages for exercising the impact analysis checks.
--
-- Production (baseline) versions. Build the lab first with
-- lab/demo_schema/apply.py, then run this file in the Production database (or
-- the base edition) followed by 02_test_cases.sql in Test.
--
-- Each package trips one detector, so a report has one clear headline finding.
-- Expected results with the investigator off:
--
--   DEMO.PKG_PRICE   business logic, high      Requires further investigation
--   DEMO.PKG_SETTLE  transaction, high         Not recommended for deployment
--   DEMO.PKG_REFUND  data, critical            Not recommended for deployment
--   DEMO.PKG_NOTIFY  error contract, high      Requires further investigation
--                    (plus a low executable-text note)
--
-- DEMO.CUSTOMER_BILLS and DEMO.OUTBOX come from lab/demo_schema/01_shared.sql.
--
-- Safe to re-run.

DECLARE
  PROCEDURE ddl(p_sql IN VARCHAR2, p_already_exists IN PLS_INTEGER) IS
  BEGIN
    EXECUTE IMMEDIATE p_sql;
  EXCEPTION
    WHEN OTHERS THEN
      IF SQLCODE != p_already_exists THEN
        RAISE;
      END IF;
  END ddl;
BEGIN
  -- Backing table for the Test-only REFUND_LEDGER view. The name differs from
  -- the view, so Production never has an object called REFUND_LEDGER. A plain
  -- table in Test would also work on two separate databases, but a shared
  -- table plus an editioned view is the one pattern that works both when Test
  -- is a separate database and when it is an edition of Production.
  ddl(
    'CREATE TABLE demo.refund_ledger_data ('
    || ' customer_id NUMBER NOT NULL,'
    || ' refund_date DATE NOT NULL,'
    || ' amount      NUMBER,'
    || ' CONSTRAINT pk_refund_ledger_data PRIMARY KEY (customer_id, refund_date))',
    -955
  );
END;
/

-- Case 1: financial threshold. Only the comparison value changes in Test.
CREATE OR REPLACE PACKAGE demo.pkg_price AS
  PROCEDURE apply_discount(
    p_customer_id IN NUMBER,
    p_amount      IN NUMBER
  );
END pkg_price;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_price AS
  PROCEDURE apply_discount(
    p_customer_id IN NUMBER,
    p_amount      IN NUMBER
  ) IS
    v_total NUMBER := p_amount;
  BEGIN
    IF p_amount > 100 THEN
      v_total := p_amount * 0.9;
    END IF;
    INSERT INTO customer_bills (customer_id, bill_date, amount, status)
    VALUES (p_customer_id, SYSDATE, v_total, 'PENDING');
  END apply_discount;
END pkg_price;
/

-- Case 2: transaction boundary. Test moves the COMMIT before the later UPDATE.
CREATE OR REPLACE PACKAGE demo.pkg_settle AS
  PROCEDURE close_batch(
    p_batch_id IN NUMBER
  );
END pkg_settle;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_settle AS
  PROCEDURE close_batch(
    p_batch_id IN NUMBER
  ) IS
  BEGIN
    INSERT INTO customer_bills (customer_id, bill_date, amount, status)
    VALUES (p_batch_id, SYSDATE, 0, 'PENDING');
    UPDATE outbox SET state = 'CLOSED' WHERE id = p_batch_id;
    COMMIT;
  END close_batch;
END pkg_settle;
/

-- Case 3: Production baseline. Production inserts into the existing
-- CUSTOMER_BILLS table; Test inserts into a Test-only object instead, so the
-- only new dependency is the missing one.
CREATE OR REPLACE PACKAGE demo.pkg_refund AS
  PROCEDURE issue_refund(
    p_customer_id IN NUMBER,
    p_amount      IN NUMBER
  );
END pkg_refund;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_refund AS
  PROCEDURE issue_refund(
    p_customer_id IN NUMBER,
    p_amount      IN NUMBER
  ) IS
  BEGIN
    UPDATE customer_bills
       SET status = 'REFUNDED'
     WHERE customer_id = p_customer_id;
    INSERT INTO customer_bills (customer_id, bill_date, amount, status) VALUES (p_customer_id, SYSDATE, p_amount, 'REFUNDED');
  END issue_refund;
END pkg_refund;
/

-- Case 4: error contract. Test changes the RAISE_APPLICATION_ERROR code.
CREATE OR REPLACE PACKAGE demo.pkg_notify AS
  PROCEDURE alert(
    p_event_id IN NUMBER,
    p_event    IN VARCHAR2
  );
END pkg_notify;
/

CREATE OR REPLACE PACKAGE BODY demo.pkg_notify AS
  PROCEDURE alert(
    p_event_id IN NUMBER,
    p_event    IN VARCHAR2
  ) IS
  BEGIN
    IF p_event IS NULL THEN
      RAISE_APPLICATION_ERROR(-20001, 'Event is missing');
    END IF;
    UPDATE outbox SET state = 'QUEUED' WHERE id = p_event_id;
  END alert;
END pkg_notify;
/
