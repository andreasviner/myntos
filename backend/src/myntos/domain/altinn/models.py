"""Altinn 3 systembruker requests and approvals per company.

A company approves Myntos as a system user in Altinn once, and from then on
Maskinporten issues tokens scoped to that company with no person present for
machine-to-machine government APIs such as Aksjonærregisteroppgaven (RF-1086),
Frister, and Krav og betalinger (docs/integrations/maskinporten.md).

``external_ref`` is persisted verbatim: Altinn keys the created system user on
it, and omitting it at token time causes Maskinporten to answer 404
``invalid_altinn_customer_configuration``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from myntos.db.base import Base


class CompanySystemUser(Base):
    """One Altinn 3 system-user request (pending or approved) for a company."""

    __tablename__ = "company_system_user"
    __table_args__ = (Index("ix_company_system_user_company_id", "company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    # The Altinn system id this request was created against (e.g. ``938193045_myntos``).
    system_id: Mapped[str] = mapped_column(String(64))
    # Part of the system user's identity at token time; never discarded or regenerated
    # for an approved user.
    external_ref: Mapped[str] = mapped_column(String(128))
    # Altinn's UUID for the pending or settled request.
    altinn_request_id: Mapped[str] = mapped_column(String(80), unique=True)
    # Altinn's status spelling, stored lowercase (``new``, ``accepted``, ``rejected``,
    # ``denied``, ``timedout``).
    status: Mapped[str] = mapped_column(String(32))
    # Resource ids requested (subset of systemregister.SYSTEM_RESOURCES).
    resources: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    # Access package URNs requested, if any (subset of systemregister.SYSTEM_ACCESS_PACKAGES).
    access_packages: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    # The Altinn access-management UI URL the company's representative opens to approve.
    confirm_url: Mapped[str | None] = mapped_column(String(512))
    # SET NULL: deleting a user account does not cancel the company's Altinn setup.
    requested_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_approved(self) -> bool:
        return self.status.casefold() == "accepted"

    def covers_resource(self, resource: str) -> bool:
        """Whether this approved system user was granted ``resource``."""
        return self.is_approved and resource in self.resources
