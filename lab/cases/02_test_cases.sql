-- Case packages for exercising the impact analysis checks.
--
-- Test (proposed) versions. Run this file in the Test database (or the Test
-- edition) after 01_production_cases.sql has been applied to Production.
--
-- Only the one line named in each package comment changes, so the report
-- isolates a single detector. Expected results with the investigator off:
--
--   DEMO.PKG_PRICE   business logic, high      Requires further investigation
--   DEMO.PKG_SETTLE  transaction, high         Not recommended for deployment
--   DEMO.PKG_REFUND  data, critical            Not recommended for deployment
--   DEMO.PKG_NOTIFY  error contract, high      Requires further investigation
--                    (plus a low executable-text note)
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
  -- Present in Test as a separate database automatically. When Test is an
  -- edition of Production the table is shared, so creating it again is
  -- ignored (ORA-00955). Only the view below is Test-only.
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

-- Test-only object. Production's dictionary has no object named REFUND_LEDGER,
-- and PKG_REFUND now depends on it. A view keeps this edition-safe.
CREATE OR REPLACE VIEW demo.refund_ledger AS
SELECT customer_id, refund_date, amount
  FROM demo.refund_ledger_data;
/

-- Case 1: the only change is the threshold, 100 -> 500. AMOUNT is a financial
-- keyword, so the finding is raised to high severity.
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
    IF p_amount > 500 THEN
      v_total := p_amount * 0.9;
    END IF;
    INSERT INTO customer_bills (customer_id, bill_date, amount, status)
    VALUES (p_customer_id, SYSDATE, v_total, 'PENDING');
  END apply_discount;
END pkg_price;
/

-- Case 2: the COMMIT now sits before the UPDATE, so the INSERT is durable
-- even if the UPDATE fails.
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
    COMMIT;
    UPDATE outbox SET state = 'CLOSED' WHERE id = p_batch_id;
  END close_batch;
END pkg_settle;
/

-- Case 3: the INSERT targets the Test-only REFUND_LEDGER view, which
-- Production does not have. It replaces Production's insert into an existing
-- table, so the missing object is the only new dependency.
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
    INSERT INTO demo.refund_ledger (customer_id, refund_date, amount) VALUES (p_customer_id, SYSDATE, p_amount);
  END issue_refund;
END pkg_refund;
/

-- Case 4: -20001 becomes -20099. Callers or alert rules that handle the old
-- code stop receiving it.
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
      RAISE_APPLICATION_ERROR(-20099, 'Event is missing');
    END IF;
    UPDATE outbox SET state = 'QUEUED' WHERE id = p_event_id;
  END alert;
END pkg_notify;
/
