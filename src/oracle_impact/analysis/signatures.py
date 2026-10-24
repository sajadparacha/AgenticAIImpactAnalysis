"""Signature comparison using dictionary arguments, with change-set caller checks."""

from dataclasses import dataclass, field

from oracle_impact.models.domain import Argument, PackageRef, Snapshot


@dataclass
class Param:
    name: str
    position: int
    data_type: str
    in_out: str
    defaulted: bool


@dataclass
class RoutineSignature:
    name: str
    overload: str
    params: list[Param]
    return_type: str | None


@dataclass
class SignatureChange:
    package: str
    routine: str
    kind: str
    breaking: bool
    detail: str
    new_arg_names: list[str] = field(default_factory=list)
    external_callers: list[str] = field(default_factory=list)
    coordinated_callers: list[str] = field(default_factory=list)
    unresolved: bool = False
    test_lines: list[int] = field(default_factory=list)
    prod_lines: list[int] = field(default_factory=list)


def _routines(arguments: list[Argument]) -> dict[str, list[RoutineSignature]]:
    grouped: dict[tuple[str, str], list[Argument]] = {}
    for argument in arguments:
        grouped.setdefault((argument.object_name.upper(), argument.overload or ""), []).append(argument)
    routines: dict[str, list[RoutineSignature]] = {}
    for (name, overload), args in grouped.items():
        ordered = sorted(args, key=lambda item: item.position)
        params = [
            Param(
                name=item.argument_name or "",
                position=item.position,
                data_type=(item.data_type or "").upper(),
                in_out=(item.in_out or "IN").upper(),
                defaulted=item.defaulted,
            )
            for item in ordered
            if item.position > 0 and item.argument_name
        ]
        returns = [item for item in ordered if item.position == 0 or not item.argument_name]
        signature = RoutineSignature(
            name=name,
            overload=overload,
            params=params,
            return_type=returns[0].data_type.upper() if returns else None,
        )
        routines.setdefault(name, []).append(signature)
    return routines


def _names(params: list[Param]) -> list[str]:
    return [param.name.upper() for param in params]


def _pair(prod: list[RoutineSignature], test: list[RoutineSignature]):
    if len(prod) == 1 and len(test) == 1:
        return [(prod[0], test[0])]
    used: set[int] = set()
    pairs: list[tuple[RoutineSignature | None, RoutineSignature | None]] = []
    for prod_sig in prod:
        match_index = None
        for index, test_sig in enumerate(test):
            if index in used:
                continue
            if prod_sig.overload and prod_sig.overload == test_sig.overload:
                match_index = index
                break
        if match_index is None:
            for index, test_sig in enumerate(test):
                if index in used:
                    continue
                if _names(prod_sig.params) == _names(test_sig.params):
                    match_index = index
                    break
        if match_index is None:
            pairs.append((prod_sig, None))
        else:
            used.add(match_index)
            pairs.append((prod_sig, test[match_index]))
    for index, test_sig in enumerate(test):
        if index not in used:
            pairs.append((None, test_sig))
    return pairs


def _line_of(source: str, needle: str) -> int:
    if not needle:
        return 1
    for index, line in enumerate(source.splitlines(), start=1):
        if needle.upper() in line.upper():
            return index
    return 1


def _caller_updated(source: str, new_arg_names: list[str]) -> bool:
    upper = source.upper()
    return all(name.upper() in upper for name in new_arg_names)


def compare_package_signatures(
    package: PackageRef,
    test_arguments: list[Argument] | None,
    prod_arguments: list[Argument] | None,
    test_inaccessible: bool,
    prod_inaccessible: bool,
    snapshot: Snapshot,
    selected: set[str],
    callers: list[tuple[str, str]],
) -> list[SignatureChange]:
    qualified = package.qualified
    if test_inaccessible or prod_inaccessible:
        return [
            SignatureChange(
                package=qualified,
                routine=package.name,
                kind="metadata_gap",
                breaking=False,
                unresolved=True,
                detail="Argument metadata could not be read, so interface compatibility was not decided.",
            )
        ]
    test_map = _routines(test_arguments or [])
    prod_map = _routines(prod_arguments or [])
    changes: list[SignatureChange] = []
    spec_test = snapshot.text("test", package.owner, package.name, "PACKAGE")
    spec_prod = snapshot.text("production", package.owner, package.name, "PACKAGE")

    for routine in sorted(set(prod_map) - set(test_map)):
        changes.append(
            _with_callers(
                SignatureChange(
                    package=qualified,
                    routine=routine,
                    kind="removed_routine",
                    breaking=True,
                    detail=f"{routine} is present in Production and absent from Test.",
                    prod_lines=[_line_of(spec_prod, routine)],
                ),
                callers,
                selected,
                snapshot,
            )
        )
    for routine in sorted(set(test_map) - set(prod_map)):
        changes.append(
            SignatureChange(
                package=qualified,
                routine=routine,
                kind="added_routine",
                breaking=False,
                detail=f"{routine} is new in Test.",
                test_lines=[_line_of(spec_test, routine)],
            )
        )
    for routine in sorted(set(test_map) & set(prod_map)):
        for prod_sig, test_sig in _pair(prod_map[routine], test_map[routine]):
            if prod_sig and test_sig is None:
                changes.append(
                    _with_callers(
                        SignatureChange(
                            package=qualified,
                            routine=routine,
                            kind="removed_overload",
                            breaking=True,
                            detail=f"An overload of {routine} was removed.",
                            prod_lines=[_line_of(spec_prod, routine)],
                        ),
                        callers,
                        selected,
                        snapshot,
                    )
                )
                continue
            if test_sig and prod_sig is None:
                changes.append(
                    SignatureChange(
                        package=qualified,
                        routine=routine,
                        kind="added_overload",
                        breaking=False,
                        detail=(
                            f"A new overload of {routine} was added. Existing calls can become "
                            "ambiguous, so callers still need review."
                        ),
                        test_lines=[_line_of(spec_test, routine)],
                        unresolved=True,
                    )
                )
                continue
            assert prod_sig is not None and test_sig is not None
            changes.extend(
                _compare_pair(
                    qualified,
                    routine,
                    prod_sig,
                    test_sig,
                    spec_test,
                    spec_prod,
                    callers,
                    selected,
                    snapshot,
                )
            )
    return changes


def _compare_pair(
    package: str,
    routine: str,
    prod_sig: RoutineSignature,
    test_sig: RoutineSignature,
    spec_test: str,
    spec_prod: str,
    callers: list[tuple[str, str]],
    selected: set[str],
    snapshot: Snapshot,
) -> list[SignatureChange]:
    found: list[SignatureChange] = []
    if (prod_sig.return_type or "") != (test_sig.return_type or ""):
        found.append(
            SignatureChange(
                package=package,
                routine=routine,
                kind="return_type",
                breaking=True,
                detail=(
                    f"{routine} return type changed from {prod_sig.return_type or 'none'} "
                    f"to {test_sig.return_type or 'none'}."
                ),
                test_lines=[_line_of(spec_test, routine)],
                prod_lines=[_line_of(spec_prod, routine)],
            )
        )
    prod_names = _names(prod_sig.params)
    test_names = _names(test_sig.params)
    prod_by_name = {param.name.upper(): param for param in prod_sig.params}
    test_by_name = {param.name.upper(): param for param in test_sig.params}
    if prod_names == test_names:
        for prod_param, test_param in zip(prod_sig.params, test_sig.params, strict=True):
            if prod_param.data_type != test_param.data_type:
                found.append(
                    _with_callers(
                        SignatureChange(
                            package=package,
                            routine=routine,
                            kind="type_change",
                            breaking=True,
                            detail=(
                                f"{routine}.{prod_param.name} changed from {prod_param.data_type} "
                                f"to {test_param.data_type}."
                            ),
                            test_lines=[_line_of(spec_test, test_param.name)],
                            prod_lines=[_line_of(spec_prod, prod_param.name)],
                        ),
                        callers,
                        selected,
                        snapshot,
                    )
                )
            elif prod_param.in_out != test_param.in_out:
                found.append(
                    _with_callers(
                        SignatureChange(
                            package=package,
                            routine=routine,
                            kind="mode_change",
                            breaking=True,
                            detail=(
                                f"{routine}.{prod_param.name} mode changed from {prod_param.in_out} "
                                f"to {test_param.in_out}."
                            ),
                            test_lines=[_line_of(spec_test, test_param.name)],
                            prod_lines=[_line_of(spec_prod, prod_param.name)],
                        ),
                        callers,
                        selected,
                        snapshot,
                    )
                )
        return found

    removed = [name for name in prod_names if name not in test_by_name]
    if removed:
        found.append(
            _with_callers(
                SignatureChange(
                    package=package,
                    routine=routine,
                    kind="removed_parameter",
                    breaking=True,
                    detail=f"{routine} no longer declares {', '.join(removed)}.",
                    prod_lines=[_line_of(spec_prod, removed[0])],
                    test_lines=[_line_of(spec_test, routine)],
                ),
                callers,
                selected,
                snapshot,
            )
        )
        return found

    added = [test_by_name[name] for name in test_names if name not in prod_by_name]
    prefix_ok = test_names[: len(prod_names)] == prod_names
    appended = prefix_ok and len(test_names) == len(prod_names) + len(added)
    if appended and all(param.defaulted for param in added):
        names = [param.name for param in added]
        found.append(
            SignatureChange(
                package=package,
                routine=routine,
                kind="compatible_optional",
                breaking=False,
                detail=(
                    f"{routine} gained optional parameter(s) {', '.join(names)} with defaults, "
                    "appended after the existing parameters."
                ),
                new_arg_names=names,
                test_lines=[_line_of(spec_test, names[0])],
                prod_lines=[_line_of(spec_prod, routine)],
            )
        )
        return found

    names = [param.name for param in added]
    found.append(
        _with_callers(
            SignatureChange(
                package=package,
                routine=routine,
                kind="added_parameter",
                breaking=True,
                detail=(
                    f"{routine} gained parameter(s) {', '.join(names) or 'unnamed'} without a "
                    "compatible defaulted suffix."
                ),
                new_arg_names=names,
                test_lines=[_line_of(spec_test, names[0] if names else routine)],
                prod_lines=[_line_of(spec_prod, routine)],
            ),
            callers,
            selected,
            snapshot,
        )
    )
    return found


def _with_callers(
    change: SignatureChange,
    callers: list[tuple[str, str]],
    selected: set[str],
    snapshot: Snapshot,
) -> SignatureChange:
    if not change.breaking or change.kind not in {"added_parameter", "removed_routine", "removed_overload"}:
        if change.breaking:
            change.external_callers = [f"{owner}.{name}" for owner, name in callers if f"{owner}.{name}" not in selected]
            if change.external_callers:
                change.detail += f" Production callers outside the change set: {', '.join(change.external_callers)}."
        return change

    external: list[str] = []
    coordinated: list[str] = []
    unresolved = False
    for owner, name in callers:
        qualified = f"{owner}.{name}"
        if qualified not in selected:
            external.append(qualified)
            continue
        source = "\n".join(
            snapshot.text("test", owner, name, object_type)
            for object_type in ("PACKAGE", "PACKAGE BODY")
        )
        if not source.strip():
            unresolved = True
            continue
        if change.kind == "added_parameter" and _caller_updated(source, change.new_arg_names):
            coordinated.append(qualified)
        elif change.kind in {"removed_routine", "removed_overload"} and change.routine.upper() not in source.upper():
            coordinated.append(qualified)
        else:
            external.append(qualified)
    change.external_callers = external
    change.coordinated_callers = coordinated
    change.unresolved = unresolved
    if external or unresolved or not callers:
        change.breaking = True
    else:
        change.breaking = False
        change.detail += (
            " Every visible database caller is in this change set and was updated. "
            "Callers outside the database remain unknown."
        )
    if external:
        change.detail += f" Unupdated callers: {', '.join(external)}."
    return change
