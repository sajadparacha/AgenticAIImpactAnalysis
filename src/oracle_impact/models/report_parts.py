"""Source diff records used by the report."""

from pydantic import BaseModel, Field


class DiffRow(BaseModel):
    test_line_no: int | None = None
    test_text: str = ""
    test_kind: str = "empty"
    prod_line_no: int | None = None
    prod_text: str = ""
    prod_kind: str = "empty"


class ObjectDiff(BaseModel):
    owner: str
    name: str
    object_type: str
    test_hash: str | None = None
    prod_hash: str | None = None
    added: int = 0
    removed: int = 0
    formatting_only: bool = False
    unchanged: bool = False
    rows: list[DiffRow] = Field(default_factory=list)
