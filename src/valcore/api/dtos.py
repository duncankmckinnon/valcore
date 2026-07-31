"""Pydantic request/response schemas for the API surface.

These are kept separate from the SQLModel entities so responses never leak raw SQL
objects or secrets. Only the shared error envelope lives here; resource routers define
their own request/response models.
"""

from pydantic import BaseModel


class ErrorBody(BaseModel):
    """The inner error payload: the exception class name and its message."""

    type: str
    message: str


class ErrorResponse(BaseModel):
    """Uniform error envelope returned by every exception handler."""

    error: ErrorBody
