from typing import Annotated

from pydantic import BaseModel, Field

# The range of a PostgreSQL INTEGER key. Beyond it the database does not answer
# "not found": it fails, and a model can write any number.
DatabaseId = Annotated[int, Field(ge=1, le=2**31 - 1)]


class Page[T](BaseModel):
    """A slice of a list plus the total, so clients can paginate."""

    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    """Body of every error response (same shape as FastAPI's HTTPException)."""

    detail: str
