"""Capture source snapshots and detect drift inside a single environment."""

from oracle_impact.analysis.hashing import source_hash
from oracle_impact.infrastructure.oracle.gateway import OracleAccessError, OracleUnavailable, utc_now
from oracle_impact.models.domain import CapturedObject, PackageRef, Snapshot

_TYPES = ("PACKAGE", "PACKAGE BODY")


class SnapshotService:
    def __init__(self, test_gateway, production_gateway, autumn_gateway=None) -> None:
        self._gateways = {"test": test_gateway, "production": production_gateway}
        self._optional = {"autumn": autumn_gateway} if autumn_gateway is not None else {}

    @property
    def environments(self) -> list[str]:
        return list(self._gateways) + list(self._optional)

    def capture(self, packages: list[PackageRef]) -> Snapshot:
        snapshot = Snapshot(packages=[package.qualified for package in packages], captured_at=utc_now())
        missing: list[str] = []
        unavailable: set[str] = set()
        for package in packages:
            for object_type in _TYPES:
                for environment, gateway in self._gateways.items():
                    snapshot.objects.append(self._capture(gateway, environment, package.owner, package.name, object_type))
                for environment, gateway in self._optional.items():
                    if environment in unavailable:
                        continue
                    try:
                        snapshot.objects.append(self._capture(gateway, environment, package.owner, package.name, object_type))
                    except (OracleAccessError, OracleUnavailable):
                        unavailable.add(environment)
                        snapshot.limitations.append(
                            f"The {getattr(gateway, 'label', environment)} environment could not be read. "
                            "Its comparison is omitted from this report."
                        )
            test_spec = snapshot.find("test", package.owner, package.name, "PACKAGE")
            test_body = snapshot.find("test", package.owner, package.name, "PACKAGE BODY")
            if test_spec and test_body and not test_spec.found and not test_body.found:
                if test_spec.inaccessible or test_body.inaccessible:
                    snapshot.limitations.append(
                        f"{package.qualified} could not be read in Test. Metadata or source access was denied."
                    )
                else:
                    missing.append(package.qualified)
        if missing:
            raise LookupError(
                "These packages were not found in Test: " + ", ".join(missing) + "."
            )
        for environment in unavailable:
            snapshot.objects = [item for item in snapshot.objects if item.environment != environment]
        for item in snapshot.objects:
            if item.drift:
                snapshot.limitations.append(
                    f"{item.environment} {item.owner}.{item.name} {item.object_type} changed during capture."
                )
        return snapshot

    def _capture(self, gateway, environment: str, owner: str, name: str, object_type: str) -> CapturedObject:
        record = gateway.fetch_source(owner, name, object_type)
        verification = gateway.verify_hash(owner, name, object_type)
        drift = bool(record.found and record.sha256 and verification and verification != record.sha256)
        return CapturedObject(
            environment=environment,
            database_id=gateway.database_id,
            owner=owner,
            name=name,
            object_type=object_type,
            found=record.found,
            inaccessible=record.inaccessible,
            source=record.source,
            sha256=record.sha256 or (source_hash(record.source) if record.source else None),
            verification_sha256=verification,
            drift=drift,
            retrieved_at=record.retrieved_at,
        )
