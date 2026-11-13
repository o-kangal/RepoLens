"""User models."""

import os.path
from dataclasses import dataclass, field as dc_field

VERSION = 2  # bump on schema change
def top_level(name):
    return os.path.join("users", name)


@dataclass(frozen=True)
class User:
    """A user account."""

    name: str
    tags: list[str] = dc_field(default_factory=list)

    @property
    def slug(self) -> str:
        return top_level(self.name)

    class Meta:
        table = "users"

    active: bool = True


async def load(user_id: int) -> User:
    def helper() -> str:
        return str(user_id)

    return User(name=helper())
