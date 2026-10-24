-- Removes the new-check lab objects. Run in each database as a DBA account.

DECLARE
  PROCEDURE drop_if_exists(p_sql IN VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE p_sql;
  EXCEPTION
    WHEN OTHERS THEN
      IF SQLCODE NOT IN (-942, -1418, -1919, -2289, -4043) THEN
        RAISE;
      END IF;
  END drop_if_exists;
BEGIN
  drop_if_exists('DROP PACKAGE impact_lab.order_api');
  drop_if_exists('DROP PACKAGE impact_lab.order_pkg');
  drop_if_exists('DROP PACKAGE impact_lab.refund_pkg');
  drop_if_exists('DROP PACKAGE impact_lab.access_pkg');
  drop_if_exists('DROP TABLE impact_lab.order_header PURGE');
  drop_if_exists('DROP TABLE impact_lab.refund_request PURGE');
  drop_if_exists('DROP TABLE impact_lab.refund_log PURGE');
  drop_if_exists('DROP TABLE impact_lab.customer_access PURGE');
  drop_if_exists('DROP TABLE impact_lab.access_audit PURGE');
  drop_if_exists('DROP SEQUENCE impact_lab.order_seq');
  drop_if_exists('DROP ROLE impact_support_role');
  drop_if_exists('DROP ROLE impact_batch_role');
  drop_if_exists('DROP ROLE impact_legacy_role');
END;
/
