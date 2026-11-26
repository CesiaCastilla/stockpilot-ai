"""Query and path parameters shared by the routes."""

from typing import Annotated

from fastapi import Path, Query

from stockpilot.schemas.common import MAX_INTEGER, NoNul

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=MAX_INTEGER)]
# Its format is not checked: a malformed VIN is just a unit that does not exist.
VinPath = Annotated[str, Path(), NoNul]
