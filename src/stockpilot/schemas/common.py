from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

# The largest PostgreSQL INTEGER. Beyond it the database does not answer "not
# found": it fails. Clients and AI models can send any number.
MAX_INTEGER = 2**31 - 1

DatabaseId = Annotated[int, Field(ge=1, le=MAX_INTEGER)]


def _reject_nul(text: str) -> str:
    if "\x00" in text:
        raise ValueError("cannot contain NUL characters")
    return text


# PostgreSQL text cannot hold a NUL character. A validator and not a pattern:
# it adds no noise to the OpenAPI or to the schema that the AI model reads.
NoNul = AfterValidator(_reject_nul)


class Page[T](BaseModel):
    """A slice of a list plus the total, so clients can paginate."""

    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    """Body of every error response (same shape as FastAPI's HTTPException)."""

    detail: str
