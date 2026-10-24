-- Shared objects for the new-check lab packages.
-- Run in BOTH the Production and the Test database, connected as a DBA account
-- (roles and objects in another schema are created). Safe to re-run.
--
-- Run order: 01_shared_setup.sql (both) -> 02_production.sql (Production) -> 03_test.sql (Test)
-- Then analyze: IMPACT_LAB.ORDER_PKG, IMPACT_LAB.REFUND_PKG, IMPACT_LAB.ACCESS_PKG

SET SERVEROUTPUT ON

DECLARE
  PROCEDURE run_ddl(p_sql IN VARCHAR2, p_already_exists IN PLS_INTEGER) IS
  BEGIN
    EXECUTE IMMEDIATE p_sql;
  EXCEPTION
    WHEN OTHERS THEN
      IF SQLCODE != p_already_exists THEN
        RAISE;
      END IF;
  END run_ddl;
BEGIN
  run_ddl('CREATE SEQUENCE impact_lab.order_seq START WITH 1000', -955);
  run_ddl(
    'CREATE TABLE impact_lab.order_header ('
    || ' order_id    NUMBER PRIMARY KEY,'
    || ' customer_id NUMBER NOT NULL,'
    || ' amount      NUMBER(12,2) NOT NULL,'
    || ' status      VARCHAR2(20) DEFAULT ''NEW'' NOT NULL,'
    || ' carrier     VARCHAR2(20))',
    -955
  );
  run_ddl(
    'CREATE TABLE impact_lab.refund_request ('
    || ' request_id NUMBER PRIMARY KEY,'
    || ' order_id   NUMBER NOT NULL,'
    || ' amount     NUMBER(12,2),'
    || ' status     VARCHAR2(20) NOT NULL)',
    -955
  );
  run_ddl(
    'CREATE TABLE impact_lab.refund_log ('
    || ' request_id NUMBER NOT NULL,'
    || ' message    VARCHAR2(200),'
    || ' logged_at  TIMESTAMP NOT NULL)',
    -955
  );
  run_ddl(
    'CREATE TABLE impact_lab.customer_access ('
    || ' customer_id NUMBER NOT NULL,'
    || ' granted_to  VARCHAR2(128) NOT NULL)',
    -955
  );
  run_ddl(
    'CREATE TABLE impact_lab.access_audit ('
    || ' customer_id NUMBER NOT NULL,'
    || ' accessed_by VARCHAR2(128) NOT NULL,'
    || ' accessed_at TIMESTAMP NOT NULL)',
    -955
  );
  run_ddl('CREATE ROLE impact_support_role', -1921);
  run_ddl('CREATE ROLE impact_batch_role', -1921);
  run_ddl('CREATE ROLE impact_legacy_role', -1921);
END;
/

MERGE INTO impact_lab.order_header o
USING (
  SELECT 1 AS order_id, 501 AS customer_id, 120.50 AS amount, 'NEW' AS status FROM dual UNION ALL
  SELECT 2, 502, 89.99, 'SHIPPED' FROM dual
) s
ON (o.order_id = s.order_id)
WHEN NOT MATCHED THEN
  INSERT (order_id, customer_id, amount, status) VALUES (s.order_id, s.customer_id, s.amount, s.status);

MERGE INTO impact_lab.refund_request r
USING (
  SELECT 1 AS request_id, 2 AS order_id, 40 AS amount, 'ISSUED' AS status FROM dual UNION ALL
  SELECT 2, 1, NULL, 'REQUESTED' FROM dual
) s
ON (r.request_id = s.request_id)
WHEN NOT MATCHED THEN
  INSERT (request_id, order_id, amount, status) VALUES (s.request_id, s.order_id, s.amount, s.status);

MERGE INTO impact_lab.customer_access a
USING (SELECT 501 AS customer_id, 'IMPACT_LAB' AS granted_to FROM dual) s
ON (a.customer_id = s.customer_id AND a.granted_to = s.granted_to)
WHEN NOT MATCHED THEN
  INSERT (customer_id, granted_to) VALUES (s.customer_id, s.granted_to);

COMMIT;
