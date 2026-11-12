-- Removes the DEMO lab. Run as a DBA account (SYSTEM).
--
-- DROP USER ... CASCADE removes every edition of the DEMO objects. The
-- OIA_TEST edition and the OIA_READER account are left in place because the
-- other lab uses them.

DECLARE
  PROCEDURE ddl(p_sql IN VARCHAR2, p_ignore IN PLS_INTEGER) IS
  BEGIN
    EXECUTE IMMEDIATE p_sql;
  EXCEPTION
    WHEN OTHERS THEN
      IF SQLCODE != p_ignore THEN
        RAISE;
      END IF;
  END ddl;
BEGIN
  ddl('DROP USER demo CASCADE', -1918);
  ddl('DROP ROLE demo_app', -1919);
  ddl('DROP ROLE demo_dispatcher', -1919);
END;
/
