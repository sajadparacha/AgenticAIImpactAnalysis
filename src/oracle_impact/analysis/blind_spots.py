"""Items the deterministic checks could not resolve, phrased as narrow questions for the investigator.

Selection is deterministic. The investigator's answers stay potential and never block or clear deployment.
"""

from dataclasses import dataclass, field

from oracle_impact.analysis.identifiers import ORACLE_MAINTAINED_SCHEMAS, is_identifier
from oracle_impact.models.report import Finding

DYNAMIC_SQL = "dynamic_sql"
SYNONYM = "synonym"


@dataclass
class BlindSpot:
    id: str
    kind: str
    owner: str
    name: str
    object_type: str = "PACKAGE BODY"
    lines: list[int] = field(default_factory=list)
    routine: str = ""
    anchor_id: str = ""
    synonym_owner: str = ""
    synonym_name: str = ""
    environments: list[str] = field(default_factory=list)

    @property
    def subject(self) -> str:
        if self.kind == SYNONYM:
            return f"Synonym {self.synonym_owner}.{self.synonym_name} used by {self.owner}.{self.name}"
        where = f" at Test line{'s' if len(self.lines) > 1 else ''} {', '.join(str(line) for line in self.lines[:4])}" if self.lines else ""
        return f"Dynamic SQL in {self.owner}.{self.name}.{self.routine or 'unknown routine'}{where}"

    def question(self) -> str:
        if self.kind == SYNONYM:
            places = " and ".join(self.environments) or "test"
            return (
                f"{self.id}: {self.owner}.{self.name} uses synonym {self.synonym_owner}.{self.synonym_name} in {places}. "
                "Which object does it point to in each environment, and does that object exist there?"
            )
        return (
            f"{self.id}: {self.owner}.{self.name} builds SQL while it runs in {self.routine or 'a routine'} "
            f"(Test lines {self.lines or 'not recorded'}). Which tables, views, or procedures can that statement use? "
            "Cite the lines that build the statement."
        )


def collect_blind_spots(findings: list[Finding], dependency_edges: list, limit: int) -> tuple[list[BlindSpot], int]:
    """Dynamic SQL findings and synonyms the selected packages reference. Returns the questions and how many were left out."""
    spots: list[BlindSpot] = []
    for finding in findings:
        if finding.origin == "investigator" or finding.change_category != DYNAMIC_SQL:
            continue
        spots.append(
            BlindSpot(
                id="",
                kind=DYNAMIC_SQL,
                owner=finding.object_owner,
                name=finding.object_name,
                object_type=finding.object_type or "PACKAGE BODY",
                lines=list(finding.changed_lines_test),
                routine=str(finding.attributes.get("routine") or ""),
                anchor_id=finding.id,
            )
        )
    synonyms: dict[tuple[str, str, str, str], BlindSpot] = {}
    for edge in dependency_edges:
        if edge.direction != "referenced" or edge.referenced_type != "SYNONYM":
            continue
        if edge.referenced_owner in ORACLE_MAINTAINED_SCHEMAS:
            continue
        if not (is_identifier(edge.referenced_owner) and is_identifier(edge.referenced_name)):
            continue
        key = (edge.owner, edge.name, edge.referenced_owner, edge.referenced_name)
        spot = synonyms.get(key)
        if spot is None:
            spot = synonyms[key] = BlindSpot(
                id="",
                kind=SYNONYM,
                owner=edge.owner,
                name=edge.name,
                object_type=edge.object_type,
                synonym_owner=edge.referenced_owner,
                synonym_name=edge.referenced_name,
            )
            spots.append(spot)
        if edge.database not in spot.environments:
            spot.environments.append(edge.database)
    for spot in synonyms.values():
        spot.environments.sort(key=lambda env: ("test", "production").index(env) if env in ("test", "production") else 2)
    chosen = spots[: max(limit, 0)]
    for number, spot in enumerate(chosen, start=1):
        spot.id = f"BS-{number}"
    return chosen, len(spots) - len(chosen)
