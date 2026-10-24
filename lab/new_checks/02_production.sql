-- Production (baseline) versions. Run in the PRODUCTION database as a DBA account,
-- after 01_shared_setup.sql.

-- ORDER_PKG: the interface callers use today.
CREATE OR REPLACE PACKAGE impact_lab.order_pkg AS
  PROCEDURE place_order(p_customer_id IN NUMBER, p_amount IN NUMBER);
  PROCEDURE get_quote(p_order_id IN NUMBER, p_total OUT NUMBER);
  PROCEDURE cancel_order(p_order_id IN NUMBER, p_reason IN VARCHAR2);
  PROCEDURE ship_order(p_order_id IN NUMBER);
  FUNCTION order_status(p_order_id IN NUMBER) RETURN VARCHAR2;
  PROCEDURE legacy_reprice(p_order_id IN NUMBER);
END order_pkg;
/

CREATE OR REPLACE PACKAGE BODY impact_lab.order_pkg AS
  PROCEDURE place_order(p_customer_id IN NUMBER, p_amount IN NUMBER) IS
  BEGIN
    INSERT INTO impact_lab.order_header (order_id, customer_id, amount, status)
    VALUES (impact_lab.order_seq.NEXTVAL, p_customer_id, p_amount, 'NEW');
  END place_order;

  PROCEDURE get_quote(p_order_id IN NUMBER, p_total OUT NUMBER) IS
  BEGIN
    SELECT amount INTO p_total FROM impact_lab.order_header WHERE order_id = p_order_id;
  END get_quote;

  PROCEDURE cancel_order(p_order_id IN NUMBER, p_reason IN VARCHAR2) IS
  BEGIN
    UPDATE impact_lab.order_header SET status = 'CANCELLED' WHERE order_id = p_order_id;
  END cancel_order;

  PROCEDURE ship_order(p_order_id IN NUMBER) IS
  BEGIN
    UPDATE impact_lab.order_header SET status = 'SHIPPED' WHERE order_id = p_order_id;
  END ship_order;

  FUNCTION order_status(p_order_id IN NUMBER) RETURN VARCHAR2 IS
    v_status impact_lab.order_header.status%TYPE;
  BEGIN
    SELECT status INTO v_status FROM impact_lab.order_header WHERE order_id = p_order_id;
    RETURN v_status;
  END order_status;

  PROCEDURE legacy_reprice(p_order_id IN NUMBER) IS
  BEGIN
    UPDATE impact_lab.order_header SET amount = ROUND(amount) WHERE order_id = p_order_id;
  END legacy_reprice;
END order_pkg;
/

-- ORDER_API: an existing Production caller of ORDER_PKG. It is not changed by the release.
CREATE OR REPLACE PACKAGE impact_lab.order_api AS
  PROCEDURE nightly_close(p_order_id IN NUMBER);
END order_api;
/

CREATE OR REPLACE PACKAGE BODY impact_lab.order_api AS
  PROCEDURE nightly_close(p_order_id IN NUMBER) IS
    v_total  NUMBER;
    v_status VARCHAR2(20);
  BEGIN
    impact_lab.order_pkg.get_quote(p_order_id, v_total);
    v_status := impact_lab.order_pkg.order_status(p_order_id);
    IF v_status = 'NEW' THEN
      impact_lab.order_pkg.cancel_order(p_order_id, 'Not shipped by close of day');
    END IF;
    impact_lab.order_pkg.legacy_reprice(p_order_id);
  END nightly_close;
END order_api;
/

-- REFUND_PKG: today's error contract and transaction behavior.
CREATE OR REPLACE PACKAGE impact_lab.refund_pkg AS
  e_refund_locked EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_refund_locked, -54);
  e_refund_limit EXCEPTION;
  PROCEDURE issue_refund(p_request_id IN NUMBER, p_amount IN NUMBER);
  PROCEDURE void_refund(p_request_id IN NUMBER);
  PROCEDURE log_refund(p_request_id IN NUMBER, p_message IN VARCHAR2);
END refund_pkg;
/

CREATE OR REPLACE PACKAGE BODY impact_lab.refund_pkg AS
  PROCEDURE log_refund(p_request_id IN NUMBER, p_message IN VARCHAR2) IS
  BEGIN
    INSERT INTO impact_lab.refund_log (request_id, message, logged_at)
    VALUES (p_request_id, p_message, SYSTIMESTAMP);
  END log_refund;

  PROCEDURE issue_refund(p_request_id IN NUMBER, p_amount IN NUMBER) IS
  BEGIN
    IF p_amount > 500 THEN
      RAISE_APPLICATION_ERROR(-20031, 'Refund is above the approval limit');
    END IF;
    UPDATE impact_lab.refund_request SET status = 'ISSUED', amount = p_amount WHERE request_id = p_request_id;
    log_refund(p_request_id, 'Refund issued');
  END issue_refund;

  PROCEDURE void_refund(p_request_id IN NUMBER) IS
    v_status impact_lab.refund_request.status%TYPE;
  BEGIN
    SELECT status INTO v_status FROM impact_lab.refund_request WHERE request_id = p_request_id FOR UPDATE NOWAIT;
    IF v_status <> 'ISSUED' THEN
      RAISE_APPLICATION_ERROR(-20032, 'Only issued refunds can be voided');
    END IF;
    UPDATE impact_lab.refund_request SET status = 'VOID' WHERE request_id = p_request_id;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      NULL;
  END void_refund;
END refund_pkg;
/

-- ACCESS_PKG: definer rights today.
CREATE OR REPLACE PACKAGE impact_lab.access_pkg AS
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

GRANT EXECUTE ON impact_lab.access_pkg TO impact_batch_role;
GRANT EXECUTE ON impact_lab.access_pkg TO impact_legacy_role;
