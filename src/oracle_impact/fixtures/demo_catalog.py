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


def _status_arguments(include_history: bool) -> list[Argument]:
    rows = [
        _arg("GET_EMPLOYEE_STATUS", None, 0, "VARCHAR2", "OUT"),
        _arg("GET_EMPLOYEE_STATUS", "P_EMPLOYEE_ID", 1, "NUMBER"),
    ]
    if include_history:
        rows.append(_arg("GET_EMPLOYEE_STATUS", "P_INCLUDE_HISTORY", 2, "BOOLEAN", defaulted=True))
    return rows


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
        catalog.add_dependency(("DEMO", "PKG_LEGACY", "PACKAGE BODY"), ("DEMO", "PKG_BILLING", "PACKAGE"))
        employee = _employee_table()
        bills = _bills_table()
        catalog.add_table("DEMO", "EMPLOYEE_STATUS", **employee)
        catalog.add_table("DEMO", "CUSTOMER_BILLS", **bills)
    test.add_table(
        "DEMO",
        "BILLING_HISTORY",
        columns=[Column(name="CUSTOMER_ID", data_type="NUMBER"), Column(name="AMOUNT", data_type="NUMBER")],
        constraints=[Constraint(name="PK_BILLING_HISTORY", constraint_type="P", columns=["CUSTOMER_ID"])],
        triggers=[],
    )
    return test, prod
