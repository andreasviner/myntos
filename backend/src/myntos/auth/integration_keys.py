"""Integration keys: a server's own secret, which sends invoices without asking.

The owner's decision of 2026-11-26, docs/agents-and-api.md rule 15. An AI
agent's send always waits for its person (rule 5), because whatever an agent
is told to do it may do for somebody else's reasons, and the key it holds was
pasted into a chat. A server is different on both counts: an owner wrote the
program, chose what it does, and put the secret in its settings, not in a
conversation. So an owner may make a key for a server that:

- is a static bearer secret (``myntos_ik_``), shown once and stored as a
  digest, with no connection key, no refresh chain and no yearly expiry: a
  server cannot paste a fresh key in, and invoicing that stops one morning
  with nobody told is worse than a key the owner sees, with its last use, on
  the page;
- works only from the IP addresses the owner gave it, so the secret copied
  anywhere else opens nothing;
- calls only the routes that declare its ``purpose`` (``x-myntos-integration``
  on the route, :data:`myntos.api.deps.INTEGRATION_EXTENSION`), which for
  ``faktura`` is making and sending invoices and nothing else: never a
  payment, a credit note, a filing or access;
- and works as the owner who made it, with their role as it stands on each
  request, so it stops when they stop being an owner.

The row is an :class:`~myntos.domain.identity.models.AgentKey` with ``kind``
``integration``, so the voucher stamp, the refusal counter, the request limit
and the stops for the person's events follow it without anything new
(:mod:`myntos.auth.agent_keys`). What differs is here, and the gate that
admits it is :func:`myntos.api.deps.get_caller`.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.auth.agent_keys import INTEGRATION_KEY_PREFIX, KeyAccess, note_use
from myntos.auth.tokens import generate_token, hash_token
from myntos.core.clock import utc_now
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.identity.errors import (
    AgentKeyNotFoundError,
    BankidAccountError,
    IntegrationKeyStoppedError,
    InvalidIntegrationAddressError,
    InvalidIntegrationKeyError,
)
from myntos.domain.identity.models import (
    AgentKey,
    AgentKeyKind,
    CompanyMembership,
    IntegrationPurpose,
    OrganizationRole,
    User,
)
from myntos.domain.identity.service import is_bankid_account
from myntos.domain.ledger.models import Company

MAX_ADDRESSES = 10

# The widest range a key may be bound to. An address list is only worth having
# if a copied secret is useless from almost everywhere, and "0.0.0.0/0" or a
# whole provider's block would make the field a formality. A /16 still covers
# a cloud region's NAT pool; a /48 is what one IPv6 site is normally given.
_WIDEST = {4: 16, 6: 48}

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def is_integration_key(token: str) -> bool:
    return token.startswith(INTEGRATION_KEY_PREFIX)


# --- Addresses --------------------------------------------------------------------


def _unmapped(network: _Network) -> _Network:
    """``::ffff:192.0.2.7`` as ``192.0.2.7``: one address, written the IPv4 way."""
    if isinstance(network, ipaddress.IPv6Network) and network.prefixlen >= 96:
        mapped = network.network_address.ipv4_mapped
        if mapped is not None:
            return ipaddress.IPv4Network(f"{mapped}/{network.prefixlen - 96}")
    return network


def parse_addresses(entries: Iterable[str]) -> tuple[str, ...]:
    """The addresses an owner typed, as the CIDR networks the row stores.

    A single address is kept as a /32 or /128, so every entry is read the same
    way. Host bits are dropped (``192.0.2.7/24`` is ``192.0.2.0/24``), the way
    a firewall reads it, rather than refused, because what the owner meant is
    not in doubt. Duplicates collapse; the order is the addresses' own.
    """
    found: set[_Network] = set()
    for raw in entries:
        entry = raw.strip()
        try:
            network = _unmapped(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            raise InvalidIntegrationAddressError(
                f"«{entry}» er ikke en IP-adresse eller et adresseområde. Skriv for eksempel "
                "203.0.113.7, 203.0.113.0/24 eller 2001:db8::1."
            ) from None
        if network.prefixlen < _WIDEST[network.version]:
            raise InvalidIntegrationAddressError(
                f"«{entry}» er for mange adresser for en nøkkel. Det største området som "
                f"godtas er /{_WIDEST[network.version]}: skriv serverens egen adresse, eller "
                "et lite område rundt den."
            )
        found.add(network)
    if not found:
        raise InvalidIntegrationAddressError("skriv minst en IP-adresse nøkkelen skal brukes fra")
    if len(found) > MAX_ADDRESSES:
        raise InvalidIntegrationAddressError(
            f"en nøkkel kan ha høyst {MAX_ADDRESSES} adresser eller adresseområder"
        )
    ordered = sorted(found, key=lambda net: (net.version, int(net.network_address), net.prefixlen))
    return tuple(network.with_prefixlen for network in ordered)


def _networks(stored: str) -> list[_Network]:
    return [ipaddress.ip_network(word) for word in stored.split()]


def shown_addresses(stored: str | None) -> list[str]:
    """The stored networks as a person wrote them: one address without its /32."""
    if not stored:
        return []
    return [
        str(network.network_address)
        if network.prefixlen == network.max_prefixlen
        else network.with_prefixlen
        for network in _networks(stored)
    ]


def address_allowed(stored: str | None, host: str | None) -> bool:
    """Whether a request from ``host`` may use a key bound to ``stored``.

    ``host`` is ``request.client.host``: the peer, or the address the proxy in
    front of Myntos put in X-Forwarded-For, which uvicorn trusts from
    127.0.0.1 only (docs/agents-and-api.md rule 15 says what that means for a
    server on the same machine). Anything that is not an IP address, which is
    what a test client or a Unix socket reports, is refused.
    """
    if not stored or not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in _networks(stored))


# --- Issuing ----------------------------------------------------------------------


@dataclass(frozen=True)
class IssuedIntegrationKey:
    """A new integration key and its secret, to show the owner once.

    The secret exists only here and in the response; the row holds its digest.
    """

    key: AgentKey
    secret: str


async def issue_integration_key(
    db: AsyncSession,
    *,
    company: Company,
    membership: CompanyMembership,
    user: User,
    name: str,
    addresses: tuple[str, ...],
    purpose: IntegrationPurpose,
) -> IssuedIntegrationKey:
    """Make an integration key for ``company``, working as ``user``, an owner.

    Not refused while the Tilgang switch is off: the switch is about AI agents
    and the programs a member connects, and an integration is the owner's own
    server, made and stopped on its own (rule 15). Refused for an account that
    signs in with BankID, for the reason :func:`myntos.auth.agent_keys.issue_key`
    gives, which a key with no expiry only strengthens.
    """
    require_permission(membership.role, Permission.INTEGRATION_MANAGE)
    if await is_bankid_account(db, user.id):
        raise BankidAccountError(
            "en konto som logger inn med BankID kan ikke lage nøkler ennå. Vi har spurt BankID "
            "om vilkårene deres tillater det, og slår det på hvis svaret er ja."
        )
    secret = INTEGRATION_KEY_PREFIX + generate_token()
    key = AgentKey(
        company_id=company.id,
        user_id=user.id,
        name=name,
        kind=AgentKeyKind.INTEGRATION,
        # No scopes: what an integration key may call is its purpose, declared
        # on each route, not the three words an agent key chooses from.
        scopes="",
        approval_scopes="",
        secret_hash=hash_token(secret),
        allowed_ips=" ".join(addresses),
        purpose=purpose,
        created_at=utc_now(),
    )
    db.add(key)
    await db.flush()
    return IssuedIntegrationKey(key=key, secret=secret)


# --- Every request ----------------------------------------------------------------


async def resolve_integration_key(db: AsyncSession, secret: str) -> KeyAccess:
    """The live integration key behind a secret, and the owner it works as.

    One round trip, as :func:`myntos.auth.agent_keys.resolve_access_token`
    makes, for the same reason. The address and the route are the gate's to
    check (:func:`myntos.api.deps.get_caller`), because a refusal for either is
    counted against the key and this only finds it.
    """
    row = (
        await db.execute(
            select(AgentKey, User, Company.name, CompanyMembership.role)
            .join(User, User.id == AgentKey.user_id)
            .join(Company, Company.id == AgentKey.company_id)
            .outerjoin(
                CompanyMembership,
                and_(
                    CompanyMembership.company_id == AgentKey.company_id,
                    CompanyMembership.user_id == AgentKey.user_id,
                ),
            )
            .where(
                AgentKey.secret_hash == hash_token(secret),
                AgentKey.kind == AgentKeyKind.INTEGRATION,
            )
        )
    ).first()
    if row is None:
        raise InvalidIntegrationKeyError(
            "ukjent integrasjonsnøkkel. Sjekk at hele nøkkelen ble kopiert, med myntos_ik_ foran."
        )
    key, user, company_name, role = row
    if key.revoked_at is not None:
        raise IntegrationKeyStoppedError(
            "integrasjonsnøkkelen er stoppet. En eier av selskapet må lage en ny i Myntos under "
            "Innstillinger for selskapet → Integrasjoner og legge den inn i serverens "
            "innstillinger."
        )
    # The backstop for the stops the member routes make: a role changed or a
    # membership ended some other way still ends the key's work here.
    if role is not OrganizationRole.OWNER or not user.is_active:
        raise IntegrationKeyStoppedError(
            "eieren som laget integrasjonsnøkkelen er ikke lenger eier av selskapet, så nøkkelen "
            "virker ikke. En eier må lage en ny under Innstillinger for selskapet → "
            "Integrasjoner."
        )
    note_use(key, None, utc_now())
    return KeyAccess(key=key, user=user, role=role, company_name=company_name)


# --- Reading the list -------------------------------------------------------------


async def list_integration_keys(db: AsyncSession, *, company_id: int) -> list[AgentKey]:
    """The company's integration keys, newest first, stopped ones included."""
    return list(
        await db.scalars(
            select(AgentKey)
            .where(
                AgentKey.company_id == company_id,
                AgentKey.kind == AgentKeyKind.INTEGRATION,
            )
            .options(selectinload(AgentKey.user))
            .order_by(AgentKey.created_at.desc(), AgentKey.id.desc())
        )
    )


async def get_integration_key(db: AsyncSession, *, company_id: int, key_id: int) -> AgentKey:
    """One integration key, looked up with its company like every other sub-resource."""
    key = await db.scalar(
        select(AgentKey)
        .where(
            AgentKey.id == key_id,
            AgentKey.company_id == company_id,
            AgentKey.kind == AgentKeyKind.INTEGRATION,
        )
        .options(selectinload(AgentKey.user))
    )
    if key is None:
        raise AgentKeyNotFoundError("fant ingen slik integrasjonsnøkkel")
    return key


IntegrationKeyState = Literal["active", "stopped"]


def integration_key_state(key: AgentKey) -> IntegrationKeyState:
    """``active`` until stopped: an integration key has no deadline of its own."""
    return "stopped" if key.revoked_at is not None else "active"
