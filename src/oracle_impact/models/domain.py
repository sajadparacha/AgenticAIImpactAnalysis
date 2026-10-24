"""Persistence and analysis records. These models do not import the web framework."""

from pydantic import BaseModel, Field


class PackageRef(BaseModel):
    owner: str
    name: str

    @property
    def qualified(self) -> str:
        return f"{self.owner}.{self.name}"


class Argument(BaseModel):
    object_name: str
    overload: str = ""
    argument_name: str | None = None
    position: int
    data_type: str
    in_out: str = "IN"
    defaulted: bool = False


class Column(BaseModel):
    name: str
    data_type: str
    nullable: bool = True


class Constraint(BaseModel):
    name: str
    constraint_type: str
    columns: list[str] = Field(default_factory=list)
    referenced_table: str | None = None


class Trigger(BaseModel):
    name: str
    trigger_type: str
    event: str
    status: str = "ENABLED"


class TableMetadata(BaseModel):
    owner: str
    name: str
    exists: bool
    inaccessible: bool = False
    columns: list[Column] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    triggers: list[Trigger] = Field(default_factory=list)


class DependencyEdge(BaseModel):
    direction: str
    depth: int = 1
    owner: str
    name: str
    object_type: str
    referenced_owner: str
    referenced_name: str
    referenced_type: str
    database: str = ""

    @property
    def dependent_qualified(self) -> str:
        return f"{self.owner}.{self.name}"

    @property
    def referenced_qualified(self) -> str:
        return f"{self.referenced_owner}.{self.referenced_name}"


class CapturedObject(BaseModel):
    environment: str
    database_id: str
    owner: str
    name: str
    object_type: str
    found: bool
    inaccessible: bool = False
    source: str = ""
    sha256: str | None = None
    verification_sha256: str | None = None
    drift: bool = False
    retrieved_at: str


class Snapshot(BaseModel):
    packages: list[str]
    objects: list[CapturedObject] = Field(default_factory=list)
    captured_at: str
    limitations: list[str] = Field(default_factory=list)

    def find(
        self,
        environment: str,
        owner: str,
        name: str,
        object_type: str,
    ) -> CapturedObject | None:
        for item in self.objects:
            if (
                item.environment == environment
                and item.owner == owner
                and item.name == name
                and item.object_type == object_type
            ):
                return item
        return None

    def text(self, environment: str, owner: str, name: str, object_type: str) -> str:
        item = self.find(environment, owner, name, object_type)
        if item is None or not item.found or item.inaccessible:
            return ""
        return item.source
