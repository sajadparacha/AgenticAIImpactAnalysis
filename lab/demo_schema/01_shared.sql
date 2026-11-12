-- Shared objects for the DEMO lab packages.
--
-- Run as the DEMO user in the Production environment (base edition) once, before
-- 02_production.sql. If Test and Production are separate databases, run this
-- same file in both.
--
-- These objects are not editioned, so both environments see the same tables,
-- triggers, and helper function. The two fixtures that must differ per
-- environment (the Test-only BILLING_HISTORY and the Test-only dependency) are
-- created in 03_test.sql as editioned objects.
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
  ddl(
    'CREATE TABLE demo.employee_status ('
    || ' employee_id NUMBER PRIMARY KEY,'
    || ' status      VARCHAR2(50))',
    -955
  );
  ddl(
    'CREATE TABLE demo.customer_bills ('
    || ' customer_id NUMBER NOT NULL,'
    || ' bill_date   DATE NOT NULL,'
    || ' amount      NUMBER,'
    || ' status      VARCHAR2(20),'
    || ' CONSTRAINT pk_customer_bills PRIMARY KEY (customer_id, bill_date))',
    -955
  );
  ddl(
    'CREATE TABLE demo.outbox ('
    || ' id    NUMBER PRIMARY KEY,'
    || ' state VARCHAR2(20))',
    -955
  );
  ddl(
    'CREATE TABLE demo.billing_history_data ('
    || ' customer_id NUMBER NOT NULL,'
    || ' bill_date   DATE NOT NULL,'
    || ' amount      NUMBER,'
    || ' CONSTRAINT pk_billing_history_data PRIMARY KEY (customer_id, bill_date))',
    -955
  );
  ddl(
    'CREATE TABLE demo.quote_book_local ('
    || ' code  VARCHAR2(20) PRIMARY KEY,'
    || ' quote NUMBER)',
    -955
  );
  ddl(
    'CREATE TABLE demo.quote_book_prod ('
    || ' code  VARCHAR2(20) PRIMARY KEY,'
    || ' quote NUMBER)',
    -955
  );
  ddl(
    'CREATE TABLE demo.orders ('
    || ' order_id NUMBER PRIMARY KEY,'
    || ' total    NUMBER,'
    || ' status   VARCHAR2(20))',
    -955
  );
  ddl(
    'CREATE TABLE demo.order_audit_data ('
    || ' order_id NUMBER PRIMARY KEY,'
    || ' note     VARCHAR2(200))',
    -955
  );
  ddl(
    'CREATE TABLE demo.customers ('
    || ' customer_id NUMBER PRIMARY KEY,'
    || ' region      VARCHAR2(20))',
    -955
  );
END;
/

CREATE OR REPLACE TRIGGER demo.trg_status_audit
AFTER UPDATE ON demo.employee_status
FOR EACH ROW
BEGIN
  NULL;
END;
/

CREATE OR REPLACE TRIGGER demo.trg_bill_audit
AFTER INSERT OR UPDATE ON demo.customer_bills
FOR EACH ROW
BEGIN
  NULL;
END;
/

CREATE OR REPLACE TRIGGER demo.trg_outbox_audit
AFTER UPDATE ON demo.outbox
FOR EACH ROW
BEGIN
  NULL;
END;
/

CREATE OR REPLACE FUNCTION demo.calculate_amount(
  p_customer_id IN NUMBER,
  p_bill_date IN DATE
) RETURN NUMBER IS
BEGIN
  RETURN 100;
END calculate_amount;
/
