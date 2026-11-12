"""The asset inventory lookup (#12)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from socboard.models import Asset


def criticality_for_asset(session: Session, name: str) -> str | None:
    """The criticality of the asset named exactly `name` (case-sensitive, no trimming), or None."""
    return session.execute(select(Asset.criticality).where(Asset.name == name)).scalar_one_or_none()
