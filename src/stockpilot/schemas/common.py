from pydantic import BaseModel


class Page[T](BaseModel):
    """A slice of a list plus the total, so clients can paginate."""

    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    """Body of every error response (same shape as FastAPI's HTTPException)."""

    detail: str
