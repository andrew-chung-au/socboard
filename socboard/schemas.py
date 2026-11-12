"""Request and response models of the HTTP API, named as in `openapi.yaml` (#9).

Field names are the contract's (camelCase). A model here never carries a password hash: `User` is
built field by field from the database row, never from the row object as a whole.
"""

from typing import Literal

from pydantic import BaseModel

from socboard.ids import to_public_id

Role = Literal["analyst", "lead"]


class User(BaseModel):
    id: str
    username: str
    displayName: str
    role: Role

    @classmethod
    def from_row(cls, row) -> "User":
        """The public fields of a `socboard.models.User` row (never `password_hash`)."""
        return cls(
            id=to_public_id(row.id),
            username=row.username,
            displayName=row.display_name,
            role=row.role,
        )


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: User
