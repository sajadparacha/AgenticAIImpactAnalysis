-- Test (proposed) versions. Run in the TEST database as a DBA account,
-- after 01_shared_setup.sql. ORDER_API is deliberately not created here:
-- the release does not update it, so the analyzer finds it as a Production caller.

-- ORDER_PKG: interface changes.
--   place_order    new VARCHAR2 overload (call ambiguity)
--   get_quote      p_total OUT -> IN OUT (mode change)
--   cancel_order   p_reason VARCHAR2 -> NUMBER (type change)
--   ship_order     new p_carrier with a default (backward compatible)
--   order_status   returns NUMBER instead of VARCHAR2
--   legacy_reprice removed, but ORDER_API still calls it
CREATE OR REPLACE PACKAGE impact_lab.order_pkg AS
  PROCEDURE place_order(p_customer_id IN NUMBER, p_amount IN NUMBER);
  PROCEDURE place_order(p_customer_id IN NUMBER, p_amount IN VARCHAR2);
  PROCEDURE get_quote(p_order_id IN NUMBER, p_total IN OUT NUMBER);
  PROCEDURE cancel_order(p_order_id IN NUMBER, p_reason IN NUMBER);
  PROCEDURE ship_order(p_order_id IN NUMBER, p_carrier IN VARCHAR2 DEFAULT 'POST');
  FUNCTION order_status(p_order_id IN NUMBER) RETURN NUMBER;
END order_pkg;
/

CREATE OR REPLACE PACKAGE BODY impact_lab.order_pkg AS
  PROCEDURE place_order(p_customer_id IN NUMBER, p_amount IN NUMBER) IS
  BEGIN
    INSERT INTO impact_lab.order_header (order_id, customer_id, amount, status)
    VALUES (impact_lab.order_seq.NEXTVAL, p_customer_id, p_amount, 'NEW');
  END place_order;

  PROCEDURE place_order(p_customer_id IN NUMBER, p_amount IN VARCHAR2) IS
  BEGIN
    place_order(p_customer_id, TO_NUMBER(p_amount));
  END place_order;

  PROCEDURE get_quote(p_order_id IN NUMBER, p_total IN OUT NUMBER) IS
  BEGIN
    SELECT NVL(p_total, 0) + amount INTO p_total FROM impact_lab.order_header WHERE order_id = p_order_id;
  END get_quote;

  PROCEDURE cancel_order(p_order_id IN NUMBER, p_reason IN NUMBER) IS
  BEGIN
    UPDATE impact_lab.order_header SET status = 'CANCELLED' WHERE order_id = p_order_id;
  END cancel_order;

  PROCEDURE ship_order(p_order_id IN NUMBER, p_carrier IN VARCHAR2 DEFAULT 'POST') IS
  BEGIN
    UPDATE impact_lab.order_header SET status = 'SHIPPED', carrier = p_carrier WHERE order_id = p_order_id;
  END ship_order;

  FUNCTION order_status(p_order_id IN NUMBER) RETURN NUMBER IS
    v_status impact_lab.order_header.status%TYPE;
  BEGIN
    SELECT status INTO v_status FROM impact_lab.order_header WHERE order_id = p_order_id;
    RETURN CASE v_status WHEN 'NEW' THEN 1 WHEN 'SHIPPED' THEN 2 WHEN 'CANCELLED' THEN 3 ELSE 0 END;
  END order_status;
END order_pkg;
/

-- REFUND_PKG: error contract and transaction changes.
--   e_refund_locked now maps to ORA-00060 (deadlock) instead of ORA-00054 (resource busy)
--   e_refund_limit removed from the specification
--   issue_refund raises -20035 instead of -20031
--   void_refund no longer raises -20032 and no longer ignores NO_DATA_FOUND
--   log_refund becomes an autonomous transaction that commits on its own
CREATE OR REPLACE PACKAGE impact_lab.refund_pkg AS
  e_refund_locked EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_refund_locked, -60);
  PROCEDURE issue_refund(p_request_id IN NUMBER, p_amount IN NUMBER);
  PROCEDURE void_refund(p_request_id IN NUMBER);
  PROCEDURE log_refund(p_request_id IN NUMBER, p_message IN VARCHAR2);
END refund_pkg;
/

CREATE OR REPLACE PACKAGE BODY impact_lab.refund_pkg AS
  PROCEDURE log_refund(p_request_id IN NUMBER, p_message IN VARCHAR2) IS
    PRAGMA AUTONOMOUS_TRANSACTION;
  BEGIN
    INSERT INTO impact_lab.refund_log (request_id, message, logged_at)
    VALUES (p_request_id, p_message, SYSTIMESTAMP);
    COMMIT;
  END log_refund;

  PROCEDURE issue_refund(p_request_id IN NUMBER, p_amount IN NUMBER) IS
  BEGIN
    IF p_amount > 500 THEN
      RAISE_APPLICATION_ERROR(-20035, 'Refund needs manager approval');
    END IF;
    UPDATE impact_lab.refund_request SET status = 'ISSUED', amount = p_amount WHERE request_id = p_request_id;
    log_refund(p_request_id, 'Refund issued');
  END issue_refund;

  PROCEDURE void_refund(p_request_id IN NUMBER) IS
    v_status impact_lab.refund_request.status%TYPE;
  BEGIN
    SELECT status INTO v_status FROM impact_lab.refund_request WHERE request_id = p_request_id FOR UPDATE NOWAIT;
    UPDATE impact_lab.refund_request SET status = 'VOID' WHERE request_id = p_request_id;
  END void_refund;
END refund_pkg;
/

-- ACCESS_PKG: switches to invoker rights; the body is unchanged.
CREATE OR REPLACE PACKAGE impact_lab.access_pkg AUTHID CURRENT_USER AS
  FUNCTION can_view_customer(p_customer_id IN NUMBER) RETURN VARCHAR2;
  PROCEDURE record_access(p_customer_id IN NUMBER);
END access_pkg;
/

CREATE OR REPLACE PACKAGE BODY impact_lab.access_pkg AS
  FUNCTION can_view_customer(p_customer_id IN NUMBER) RETURN VARCHAR2 IS
    v_count NUMBER;
  BEGIN
    SELECT COUNT(*) INTO v_count
      FROM impact_lab.customer_access
     WHERE customer_id = p_customer_id
       AND granted_to = USER;
    RETURN CASE WHEN v_count > 0 THEN 'Y' ELSE 'N' END;
  END can_view_customer;

  PROCEDURE record_access(p_customer_id IN NUMBER) IS
  BEGIN
    INSERT INTO impact_lab.access_audit (customer_id, accessed_by, accessed_at)
    VALUES (p_customer_id, USER, SYSTIMESTAMP);
  END record_access;
END access_pkg;
/

-- Test grants a new support role that Production does not have, and lacks the
-- legacy role that Production still grants.
GRANT EXECUTE ON impact_lab.access_pkg TO impact_support_role;
GRANT EXECUTE ON impact_lab.access_pkg TO impact_batch_role;
