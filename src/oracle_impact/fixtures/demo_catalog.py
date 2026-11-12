"""Bundled catalogs for fixture mode.

These are inputs to the analysis engine, not hardcoded report conclusions.
"""

from oracle_impact.infrastructure.oracle.gateway import Catalog
from oracle_impact.models.domain import Argument, Column, Constraint, Trigger


def _arg(routine: str, name: str | None, position: int, data_type: str, in_out: str = "IN", defaulted: bool = False, overload: str = "") -> Argument:
    return Argument(
        object_name=routine,
        argument_name=name,
        position=position,
        data_type=data_type,
        in_out=in_out,
        defaulted=defaulted,
        overload=overload,
    )


_STATUS_SPEC_PROD = """PACKAGE pkg_status AS
  FUNCTION get_employee_status(
    p_employee_id IN NUMBER
  ) RETURN VARCHAR2;
END pkg_status;
"""

_STATUS_SPEC_TEST = """PACKAGE pkg_status AS
  FUNCTION get_employee_status(
    p_employee_id IN NUMBER,
    p_include_history IN BOOLEAN DEFAULT FALSE
  ) RETURN VARCHAR2;
END pkg_status;
"""

_STATUS_BODY_PROD = """PACKAGE BODY pkg_status AS
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
"""

_STATUS_BODY_TEST = """PACKAGE BODY pkg_status AS
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
"""

_DASHBOARD_BODY = """PACKAGE BODY pkg_dashboard AS
  PROCEDURE show_status(p_employee_id IN NUMBER) IS
    v_status VARCHAR2(50);
  BEGIN
    v_status := pkg_status.get_employee_status(p_employee_id);
  END show_status;
END pkg_dashboard;
"""

_BILLING_SPEC_PROD = """PACKAGE pkg_billing AS
  PROCEDURE create_invoice(
    p_customer_id IN NUMBER,
    p_bill_date IN DATE
  );
END pkg_billing;
"""

_BILLING_SPEC_TEST = """PACKAGE pkg_billing AS
  PROCEDURE create_invoice(
    p_customer_id IN NUMBER,
    p_bill_date IN DATE,
    p_include_discounts IN BOOLEAN,
    p_force_recalc IN BOOLEAN DEFAULT FALSE
  );
END pkg_billing;
"""

_BILLING_BODY_PROD = """PACKAGE BODY pkg_billing AS
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
"""

_BILLING_BODY_TEST = """PACKAGE BODY pkg_billing AS
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
"""

_LEGACY_BODY = """PACKAGE BODY pkg_legacy AS
  PROCEDURE submit_bill(p_customer_id IN NUMBER, p_bill_date IN DATE) IS
  BEGIN
    pkg_billing.create_invoice(p_customer_id, p_bill_date);
  END submit_bill;
END pkg_legacy;
"""

# PKG_DISPATCH keeps the same signature. Test adds dynamic SQL, two functions
# Production does not have, a different error contract, and AUTHID CURRENT_USER.
# QUOTE_BOOK is a synonym in both environments; each one resolves to a different
# local table, the way lab/demo_schema does it without a database link.
_DISPATCH_SPEC_PROD = """PACKAGE pkg_dispatch AS
  queue_full EXCEPTION;
  PROCEDURE send_batch(
    p_code IN VARCHAR2
  );
END pkg_dispatch;
"""

_DISPATCH_SPEC_TEST = """PACKAGE pkg_dispatch AUTHID CURRENT_USER AS
  batch_rejected EXCEPTION;
  PROCEDURE send_batch(
    p_code IN VARCHAR2
  );
END pkg_dispatch;
"""

_DISPATCH_BODY_PROD = """PACKAGE BODY pkg_dispatch AS
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
"""

_DISPATCH_BODY_TEST = """PACKAGE BODY pkg_dispatch AS
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
    v_id := dispatch_seq.NEXTVAL;
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
"""

_MAILER_SPEC = """PACKAGE pkg_mailer AS
  PROCEDURE deliver(p_code IN VARCHAR2);
END pkg_mailer;
"""

_MAILER_BODY = """PACKAGE BODY pkg_mailer AS
  PROCEDURE deliver(p_code IN VARCHAR2) IS
  BEGIN
    pkg_dispatch.send_batch(p_code);
  END deliver;
END pkg_mailer;
"""

_NEXT_BATCH = """FUNCTION next_batch(p_code IN VARCHAR2) RETURN NUMBER IS
BEGIN
  RETURN 1;
END;
"""

_NEXT_DISPATCH_ID = """FUNCTION next_dispatch_id RETURN NUMBER IS
BEGIN
  RETURN 1;
END;
"""

_BILLING_HISTORY_VIEW = "SELECT customer_id, bill_date, amount FROM demo.billing_history_data\n"

_CALCULATE_AMOUNT = """FUNCTION calculate_amount(
  p_customer_id IN NUMBER,
  p_bill_date IN DATE
) RETURN NUMBER IS
BEGIN
  RETURN 100;
END calculate_amount;
"""

# PKG_ORDER is the fixture that crowds many breaking changes into one package:
# a mandatory parameter on the specification, AUTHID CURRENT_USER, a public
# exception, a Test-only function and a Test-only view, a commit before a later
# insert, dynamic SQL, and a grant Production does not have. Its caller
# PKG_ORDER_CALLER still calls the one-argument form and no longer compiles.
_ORDER_SPEC_PROD = """PACKAGE pkg_order AS
  FUNCTION get_order_total(p_order_id IN NUMBER) RETURN NUMBER;
  PROCEDURE close_order(p_order_id IN NUMBER);
END pkg_order;
"""

_ORDER_SPEC_TEST = """PACKAGE pkg_order AUTHID CURRENT_USER AS
  order_closed EXCEPTION;
  FUNCTION get_order_total(p_order_id IN NUMBER, p_include_tax IN BOOLEAN) RETURN NUMBER;
  PROCEDURE close_order(p_order_id IN NUMBER);
END pkg_order;
"""

_ORDER_BODY_PROD = """PACKAGE BODY pkg_order AS
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
"""

_ORDER_BODY_TEST = """PACKAGE BODY pkg_order AS
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
"""

_ORDER_CALLER_SPEC = """PACKAGE pkg_order_caller AS
  PROCEDURE refresh_total(p_order_id IN NUMBER);
END pkg_order_caller;
"""

_ORDER_CALLER_BODY = """PACKAGE BODY pkg_order_caller AS
  PROCEDURE refresh_total(p_order_id IN NUMBER) IS
    v_total NUMBER;
  BEGIN
    v_total := pkg_order.get_order_total(p_order_id);
  END refresh_total;
END pkg_order_caller;
"""

_ORDER_FEE = """FUNCTION order_fee(p_order_id IN NUMBER) RETURN NUMBER IS
BEGIN
  RETURN 15;
END order_fee;
"""

_ORDER_AUDIT_VIEW = "SELECT order_id, note FROM demo.order_audit_data\n"

# PKG_LOOKUP is the fixture with no changes at all: identical specification,
# body, arguments, and grants in both environments, so the report holds no
# blocker and no required validation.
_LOOKUP_SPEC = """PACKAGE pkg_lookup AS
  FUNCTION find_region(p_customer_id IN NUMBER) RETURN VARCHAR2;
END pkg_lookup;
"""

_LOOKUP_BODY = """PACKAGE BODY pkg_lookup AS
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
"""


def _status_arguments(include_history: bool) -> list[Argument]:
    rows = [
        _arg("GET_EMPLOYEE_STATUS", None, 0, "VARCHAR2", "OUT"),
        _arg("GET_EMPLOYEE_STATUS", "P_EMPLOYEE_ID", 1, "NUMBER"),
    ]
    if include_history:
        rows.append(_arg("GET_EMPLOYEE_STATUS", "P_INCLUDE_HISTORY", 2, "BOOLEAN", defaulted=True))
    return rows


def _dispatch_arguments() -> list[Argument]:
    return [_arg("SEND_BATCH", "P_CODE", 1, "VARCHAR2")]


def _outbox_table() -> dict:
    return {
        "columns": [
            Column(name="ID", data_type="NUMBER", nullable=False),
            Column(name="STATE", data_type="VARCHAR2"),
        ],
        "constraints": [Constraint(name="PK_OUTBOX", constraint_type="P", columns=["ID"])],
        "triggers": [Trigger(name="TRG_OUTBOX_AUDIT", trigger_type="AFTER EACH ROW", event="UPDATE")],
    }


def _quote_table(constraint: str) -> dict:
    return {
        "columns": [
            Column(name="CODE", data_type="VARCHAR2", nullable=False),
            Column(name="QUOTE", data_type="NUMBER"),
        ],
        "constraints": [Constraint(name=constraint, constraint_type="P", columns=["CODE"])],
        "triggers": [],
    }


def _billing_history_data() -> dict:
    return {
        "columns": [
            Column(name="CUSTOMER_ID", data_type="NUMBER", nullable=False),
            Column(name="BILL_DATE", data_type="DATE", nullable=False),
            Column(name="AMOUNT", data_type="NUMBER"),
        ],
        "constraints": [
            Constraint(name="PK_BILLING_HISTORY_DATA", constraint_type="P", columns=["CUSTOMER_ID", "BILL_DATE"]),
        ],
        "triggers": [],
    }


def _install_dispatch(catalog: Catalog, test_copy: bool) -> None:
    """Production copy, or the Test copy when test_copy is true."""
    spec = _DISPATCH_SPEC_TEST if test_copy else _DISPATCH_SPEC_PROD
    body = _DISPATCH_BODY_TEST if test_copy else _DISPATCH_BODY_PROD
    catalog.add_source("DEMO", "PKG_DISPATCH", "PACKAGE", spec)
    catalog.add_source("DEMO", "PKG_DISPATCH", "PACKAGE BODY", body)
    catalog.add_arguments("DEMO", "PKG_DISPATCH", _dispatch_arguments())
    catalog.add_source("DEMO", "PKG_MAILER", "PACKAGE", _MAILER_SPEC)
    catalog.add_source("DEMO", "PKG_MAILER", "PACKAGE BODY", _MAILER_BODY)
    catalog.add_dependency(("DEMO", "PKG_MAILER", "PACKAGE BODY"), ("DEMO", "PKG_DISPATCH", "PACKAGE"))
    catalog.add_dependency(("DEMO", "PKG_DISPATCH", "PACKAGE BODY"), ("DEMO", "QUOTE_BOOK", "SYNONYM"))
    catalog.add_table("DEMO", "OUTBOX", **_outbox_table())
    # Both quote tables exist in each environment; only the synonym target differs.
    catalog.add_table("DEMO", "QUOTE_BOOK_LOCAL", **_quote_table("PK_QUOTE_BOOK_LOCAL"))
    catalog.add_table("DEMO", "QUOTE_BOOK_PROD", **_quote_table("PK_QUOTE_BOOK_PROD"))
    catalog.add_grant("DEMO", "PKG_DISPATCH", "DEMO_APP", "EXECUTE")
    if test_copy:
        catalog.add_synonym("DEMO", "QUOTE_BOOK", "DEMO", "QUOTE_BOOK_LOCAL")
        catalog.add_dependency(("DEMO", "PKG_DISPATCH", "PACKAGE BODY"), ("DEMO", "NEXT_DISPATCH_ID", "FUNCTION"))
        catalog.add_dependency(("DEMO", "PKG_DISPATCH", "PACKAGE BODY"), ("DEMO", "NEXT_BATCH", "FUNCTION"))
        catalog.add_source("DEMO", "NEXT_BATCH", "FUNCTION", _NEXT_BATCH)
        catalog.add_source("DEMO", "NEXT_DISPATCH_ID", "FUNCTION", _NEXT_DISPATCH_ID)
        catalog.add_grant("DEMO", "PKG_DISPATCH", "DEMO_DISPATCHER", "EXECUTE")
    else:
        catalog.add_synonym("DEMO", "QUOTE_BOOK", "DEMO", "QUOTE_BOOK_PROD")


def _billing_arguments(extended: bool) -> list[Argument]:
    rows = [
        _arg("CREATE_INVOICE", "P_CUSTOMER_ID", 1, "NUMBER"),
        _arg("CREATE_INVOICE", "P_BILL_DATE", 2, "DATE"),
    ]
    if extended:
        rows.extend(
            [
                _arg("CREATE_INVOICE", "P_INCLUDE_DISCOUNTS", 3, "BOOLEAN"),
                _arg("CREATE_INVOICE", "P_FORCE_RECALC", 4, "BOOLEAN", defaulted=True),
            ]
        )
    return rows


def _order_arguments(include_tax: bool) -> list[Argument]:
    rows = [
        _arg("GET_ORDER_TOTAL", None, 0, "NUMBER", "OUT"),
        _arg("GET_ORDER_TOTAL", "P_ORDER_ID", 1, "NUMBER"),
    ]
    if include_tax:
        rows.append(_arg("GET_ORDER_TOTAL", "P_INCLUDE_TAX", 2, "BOOLEAN"))
    rows.append(_arg("CLOSE_ORDER", "P_ORDER_ID", 1, "NUMBER"))
    return rows


def _lookup_arguments() -> list[Argument]:
    return [
        _arg("FIND_REGION", None, 0, "VARCHAR2", "OUT"),
        _arg("FIND_REGION", "P_CUSTOMER_ID", 1, "NUMBER"),
    ]


def _orders_table() -> dict:
    return {
        "columns": [
            Column(name="ORDER_ID", data_type="NUMBER", nullable=False),
            Column(name="TOTAL", data_type="NUMBER"),
            Column(name="STATUS", data_type="VARCHAR2"),
        ],
        "constraints": [Constraint(name="PK_ORDERS", constraint_type="P", columns=["ORDER_ID"])],
        "triggers": [],
    }


def _order_audit_data_table() -> dict:
    return {
        "columns": [
            Column(name="ORDER_ID", data_type="NUMBER", nullable=False),
            Column(name="NOTE", data_type="VARCHAR2"),
        ],
        "constraints": [Constraint(name="PK_ORDER_AUDIT_DATA", constraint_type="P", columns=["ORDER_ID"])],
        "triggers": [],
    }


def _customers_table() -> dict:
    return {
        "columns": [
            Column(name="CUSTOMER_ID", data_type="NUMBER", nullable=False),
            Column(name="REGION", data_type="VARCHAR2"),
        ],
        "constraints": [Constraint(name="PK_CUSTOMERS", constraint_type="P", columns=["CUSTOMER_ID"])],
        "triggers": [],
    }


def _install_order(catalog: Catalog, test_copy: bool) -> None:
    """Production copy, or the Test copy when test_copy is true."""
    spec = _ORDER_SPEC_TEST if test_copy else _ORDER_SPEC_PROD
    body = _ORDER_BODY_TEST if test_copy else _ORDER_BODY_PROD
    catalog.add_source("DEMO", "PKG_ORDER", "PACKAGE", spec)
    catalog.add_source("DEMO", "PKG_ORDER", "PACKAGE BODY", body)
    catalog.add_arguments("DEMO", "PKG_ORDER", _order_arguments(test_copy))
    catalog.add_source("DEMO", "PKG_ORDER_CALLER", "PACKAGE", _ORDER_CALLER_SPEC)
    catalog.add_source("DEMO", "PKG_ORDER_CALLER", "PACKAGE BODY", _ORDER_CALLER_BODY)
    catalog.add_dependency(("DEMO", "PKG_ORDER_CALLER", "PACKAGE BODY"), ("DEMO", "PKG_ORDER", "PACKAGE"))
    catalog.add_dependency(("DEMO", "PKG_ORDER", "PACKAGE BODY"), ("DEMO", "ORDERS", "TABLE"))
    catalog.add_table("DEMO", "ORDERS", **_orders_table())
    catalog.add_table("DEMO", "ORDER_AUDIT_DATA", **_order_audit_data_table())
    catalog.add_grant("DEMO", "PKG_ORDER", "DEMO_APP", "EXECUTE")
    if test_copy:
        catalog.add_dependency(("DEMO", "PKG_ORDER", "PACKAGE BODY"), ("DEMO", "ORDER_AUDIT", "VIEW"))
        catalog.add_dependency(("DEMO", "PKG_ORDER", "PACKAGE BODY"), ("DEMO", "ORDER_FEE", "FUNCTION"))
        catalog.add_source("DEMO", "ORDER_AUDIT", "VIEW", _ORDER_AUDIT_VIEW)
        catalog.add_dependency(("DEMO", "ORDER_AUDIT", "VIEW"), ("DEMO", "ORDER_AUDIT_DATA", "TABLE"))
        catalog.add_source("DEMO", "ORDER_FEE", "FUNCTION", _ORDER_FEE)
        catalog.add_grant("DEMO", "PKG_ORDER", "DEMO_ORDER_SVC", "EXECUTE")
        # The Test spec of PKG_ORDER added a mandatory parameter, so the existing
        # one-argument call in PKG_ORDER_CALLER no longer compiles in Test.
        catalog.set_status("DEMO", "PKG_ORDER_CALLER", "PACKAGE BODY", "INVALID")
        catalog.add_compile_error(
            "DEMO",
            "PKG_ORDER_CALLER",
            "PACKAGE BODY",
            5,
            "PLS-00306: wrong number or types of arguments in call to 'GET_ORDER_TOTAL'",
            position=14,
        )


def _install_lookup(catalog: Catalog) -> None:
    catalog.add_source("DEMO", "PKG_LOOKUP", "PACKAGE", _LOOKUP_SPEC)
    catalog.add_source("DEMO", "PKG_LOOKUP", "PACKAGE BODY", _LOOKUP_BODY)
    catalog.add_arguments("DEMO", "PKG_LOOKUP", _lookup_arguments())
    catalog.add_dependency(("DEMO", "PKG_LOOKUP", "PACKAGE BODY"), ("DEMO", "CUSTOMERS", "TABLE"))
    catalog.add_table("DEMO", "CUSTOMERS", **_customers_table())
    catalog.add_grant("DEMO", "PKG_LOOKUP", "DEMO_APP", "EXECUTE")


def _employee_table() -> dict:
    return {
        "columns": [Column(name="EMPLOYEE_ID", data_type="NUMBER", nullable=False), Column(name="STATUS", data_type="VARCHAR2")],
        "constraints": [Constraint(name="PK_EMPLOYEE_STATUS", constraint_type="P", columns=["EMPLOYEE_ID"])],
        "triggers": [Trigger(name="TRG_STATUS_AUDIT", trigger_type="AFTER EACH ROW", event="UPDATE")],
    }


def _bills_table() -> dict:
    return {
        "columns": [
            Column(name="CUSTOMER_ID", data_type="NUMBER", nullable=False),
            Column(name="BILL_DATE", data_type="DATE"),
            Column(name="AMOUNT", data_type="NUMBER"),
            Column(name="STATUS", data_type="VARCHAR2"),
        ],
        "constraints": [Constraint(name="PK_CUSTOMER_BILLS", constraint_type="P", columns=["CUSTOMER_ID", "BILL_DATE"])],
        "triggers": [Trigger(name="TRG_BILL_AUDIT", trigger_type="AFTER EACH ROW", event="INSERT OR UPDATE")],
    }


def build_demo_catalogs() -> tuple[Catalog, Catalog]:
    test = Catalog()
    prod = Catalog()
    for catalog, spec, body, status_args, billing_spec, billing_body, billing_args in (
        (prod, _STATUS_SPEC_PROD, _STATUS_BODY_PROD, _status_arguments(False), _BILLING_SPEC_PROD, _BILLING_BODY_PROD, _billing_arguments(False)),
        (test, _STATUS_SPEC_TEST, _STATUS_BODY_TEST, _status_arguments(True), _BILLING_SPEC_TEST, _BILLING_BODY_TEST, _billing_arguments(True)),
    ):
        catalog.add_source("DEMO", "PKG_STATUS", "PACKAGE", spec)
        catalog.add_source("DEMO", "PKG_STATUS", "PACKAGE BODY", body)
        catalog.add_arguments("DEMO", "PKG_STATUS", status_args)
        catalog.add_source("DEMO", "PKG_BILLING", "PACKAGE", billing_spec)
        catalog.add_source("DEMO", "PKG_BILLING", "PACKAGE BODY", billing_body)
        catalog.add_arguments("DEMO", "PKG_BILLING", billing_args)
        catalog.add_source("DEMO", "PKG_DASHBOARD", "PACKAGE", "PACKAGE pkg_dashboard AS PROCEDURE show_status(p_employee_id IN NUMBER); END;")
        catalog.add_source("DEMO", "PKG_DASHBOARD", "PACKAGE BODY", _DASHBOARD_BODY)
        catalog.add_source("DEMO", "PKG_LEGACY", "PACKAGE", "PACKAGE pkg_legacy AS PROCEDURE submit_bill(p_customer_id IN NUMBER, p_bill_date IN DATE); END;")
        catalog.add_source("DEMO", "PKG_LEGACY", "PACKAGE BODY", _LEGACY_BODY)
        catalog.add_dependency(("DEMO", "PKG_STATUS", "PACKAGE BODY"), ("DEMO", "EMPLOYEE_STATUS", "TABLE"))
        catalog.add_dependency(("DEMO", "PKG_DASHBOARD", "PACKAGE BODY"), ("DEMO", "PKG_STATUS", "PACKAGE"))
        catalog.add_dependency(("DEMO", "PKG_BILLING", "PACKAGE BODY"), ("DEMO", "CUSTOMER_BILLS", "TABLE"))
        catalog.add_dependency(("DEMO", "PKG_BILLING", "PACKAGE BODY"), ("DEMO", "CALCULATE_AMOUNT", "FUNCTION"))
        catalog.add_dependency(("DEMO", "PKG_LEGACY", "PACKAGE BODY"), ("DEMO", "PKG_BILLING", "PACKAGE"))
        employee = _employee_table()
        bills = _bills_table()
        catalog.add_table("DEMO", "EMPLOYEE_STATUS", **employee)
        catalog.add_table("DEMO", "CUSTOMER_BILLS", **bills)
        catalog.add_table("DEMO", "BILLING_HISTORY_DATA", **_billing_history_data())
        catalog.add_source("DEMO", "CALCULATE_AMOUNT", "FUNCTION", _CALCULATE_AMOUNT)
    test.add_source("DEMO", "BILLING_HISTORY", "VIEW", _BILLING_HISTORY_VIEW)
    test.add_dependency(("DEMO", "BILLING_HISTORY", "VIEW"), ("DEMO", "BILLING_HISTORY_DATA", "TABLE"))
    test.add_dependency(("DEMO", "PKG_BILLING", "PACKAGE BODY"), ("DEMO", "BILLING_HISTORY", "VIEW"))
    # The Test spec of PKG_BILLING added a mandatory parameter, so the existing
    # two-argument call in PKG_LEGACY no longer compiles in Test.
    test.set_status("DEMO", "PKG_LEGACY", "PACKAGE BODY", "INVALID")
    test.add_compile_error(
        "DEMO",
        "PKG_LEGACY",
        "PACKAGE BODY",
        4,
        "PLS-00306: wrong number or types of arguments in call to 'CREATE_INVOICE'",
        position=5,
    )
    _install_dispatch(prod, test_copy=False)
    _install_dispatch(test, test_copy=True)
    _install_order(prod, test_copy=False)
    _install_order(test, test_copy=True)
    _install_lookup(prod)
    _install_lookup(test)
    return test, prod

