"""Who may open one company's books, and how they got there.

Every route here is about **one set of books**. That is the change these
endpoints exist to express: a colleague is invited to a company, holds a role
over that company, and is removed from that company. They used to be invited to
the *account*, which handed them every client's books at once and said so
nowhere.

The routes therefore live under ``/companies/{company_id}/`` and take
:data:`~myntos.api.deps.CurrentCompany`, which resolves the company through the
caller's company memberships or answers 404. Two things follow from that, both
worth having:

- the answer already carries the caller's role over *this* company, so
  authorization is one call to :mod:`myntos.domain.identity.access` and no
  endpoint compares roles itself; and
- these routes are covered by ``tests/test_company_boundary.py`` and its
  sibling file, which read the route list out of the OpenAPI schema. A member
  list is exactly the sort of thing that leaks a name across a tenancy
  boundary, and now it cannot be added without being checked.

What the account still owns is in :mod:`myntos.api.v1.organizations`: whose
companies these are, and who may add another one.
"""

from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, EmailStr, Field

from myntos.api.deps import CurrentCompany, CurrentUser, DbSession, OutgoingMail
from myntos.auth import agent_keys
from myntos.core.clock import as_utc
from myntos.core.config import get_settings
from myntos.core.mail import Delivery
from myntos.domain.identity import service
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.identity.invitation_mail import invitation_mail
from myntos.domain.identity.models import (
    AgentKeyStop,
    CompanyMembership,
    OrganizationInvitation,
    OrganizationRole,
)

router = APIRouter(tags=["members"])


class MemberResponse(BaseModel):
    user_id: int
    email: EmailStr
    full_name: str
    role: str
    is_self: bool
    # Whether this person has proved, through ID-porten, who they are: the
    # thing that turns "an account called Andreas Lindeman" into "Andreas
    # Edvardsen Lindeman, who sits on the board". Colleagues see the fact and
    # the name; the fødselsnummer behind it never leaves the server.
    government_linked: bool = False
    # The name ID-porten gave. Null when the person has not linked, and also
    # when the deployment does not request the ``profile`` scope. A link
    # without a name is still a link.
    government_name: str | None = None
    government_verified_at: datetime | None = None


class InvitationResponse(BaseModel):
    id: int
    email: EmailStr
    role: str
    expires_at: datetime
    # Who it was addressed to, when the inviter named somebody. Set by inviting
    # straight from the register's role list, and typeable by hand.
    full_name: str | None = None
    # When it was sent, and by whom: the two things an administrator looking at
    # a pending invitation they do not recognise actually wants. ``invited_by``
    # is null when that account has since been deleted; the invitation outlives
    # its sender.
    created_at: datetime | None = None
    invited_by: str | None = None
    # The full acceptance link, returned once when the invitation is created and
    # never again — only the token's hash is stored. Myntos emails it to the
    # invitee when mail is configured; the link comes back either way, because
    # a deployment without mail still has to be able to invite somebody, and
    # because a message that bounced is one the inviter may want to pass on
    # themselves.
    invite_url: str | None = None
    # Whether the invitation was actually emailed. Three-valued in effect: True
    # for sent, False for tried and failed or never configured, and null when
    # this response is not about a fresh invitation (the listing), where nobody
    # is waiting to hear.
    email_sent: bool | None = None


class InviteRequest(BaseModel):
    email: EmailStr
    role: OrganizationRole = OrganizationRole.MEMBER
    # Optional, and a label rather than a check: the invitation is still
    # addressed to the address, and accepting it still requires signing in as
    # that address. See service.invite_member.
    full_name: str | None = Field(default=None, max_length=255)


class ChangeRoleRequest(BaseModel):
    role: OrganizationRole


def _invite_url(token: str) -> str:
    base = get_settings().frontend_base_url.rstrip("/")
    return f"{base}/invitasjon/?token={token}"


def _invitation_response(
    invitation: OrganizationInvitation,
    *,
    token: str | None = None,
    email_sent: bool | None = None,
    inviter: str | None = None,
) -> InvitationResponse:
    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        full_name=invitation.full_name,
        role=invitation.role.value,
        # as_utc, because SQLite hands the value back without its offset and an
        # expiry rendered without one is ambiguous to the client.
        expires_at=as_utc(invitation.expires_at),
        created_at=as_utc(invitation.created_at),
        # Passed in on a fresh invitation, where the inviter is the caller and
        # the relationship has not been loaded; read off the row when listing.
        invited_by=inviter
        if inviter is not None
        else (invitation.invited_by.full_name if invitation.invited_by is not None else None),
        invite_url=None if token is None else _invite_url(token),
        email_sent=email_sent,
    )


def _member_response(member: CompanyMembership, *, is_self: bool) -> MemberResponse:
    identity = member.user.government_identity
    return MemberResponse(
        user_id=member.user_id,
        email=member.user.email,
        full_name=member.user.full_name,
        role=member.role.value,
        is_self=is_self,
        government_linked=identity is not None,
        government_name=identity.full_name if identity is not None else None,
        government_verified_at=(as_utc(identity.verified_at) if identity is not None else None),
    )


@router.get("/companies/{company_id}/members")
async def list_members(context: CurrentCompany, db: DbSession) -> list[MemberResponse]:
    """Everyone who can open these books («Medlemmer», under «Tilgang» on /organisasjon/).

    One row per person, with the role they hold over this company: ``owner``
    («Eier», everything, the subscription included), ``admin``
    («Administrator», also who has access, the company's details, the
    kontoplan and closing a year), ``member`` («Medlem», enters the
    bookkeeping) or ``viewer`` («Leser», reads only). Access is per
    company, so somebody who keeps another set of books in the same account
    is not listed here and cannot open these. ``is_self`` marks the caller.

    ``government_linked`` says whether the person has proved who they are
    through ID-porten, the Norwegian state's login, and ``government_name``
    is the name ID-porten gave (null when it gave none). Colleagues see that
    it happened and the name; the fødselsnummer (national identity number)
    behind it never leaves the server.

    Every role may read the list. 404 ``company_not_found`` for a company the
    caller cannot open, the same answer whether or not it exists. No key may
    read it (403 ``not_for_agent_keys``): who has access is access
    management, which is never a key's (docs/agents-and-api.md rule 5). An
    agent that needs to know whose rights it works with asks
    ``GET /api/v1/agent/me``.
    """
    require_permission(context.membership.role, Permission.MEMBER_READ)
    members = await service.list_company_members(db, context.company.id)
    return [
        _member_response(member, is_self=member.user_id == context.membership.user_id)
        for member in members
    ]


@router.get("/companies/{company_id}/invitations")
async def list_invitations(context: CurrentCompany, db: DbSession) -> list[InvitationResponse]:
    """The invitations to these books nobody has accepted yet («Ventende invitasjoner»).

    Each names the address it went to, the role it grants, who sent it and
    when, and ``expires_at``. One past its deadline stays listed until it is
    withdrawn; it can no longer be accepted, and
    ``POST /api/v1/companies/{company_id}/invitations/{invitation_id}/send-again``
    sends it again with a new link. ``invite_url`` and ``email_sent`` are
    always null here: only a hash of the link is stored, so the link is shown
    once, when the invitation is made or sent again, and never listed.

    Owners and administrators only (``member:manage``); 403
    ``permission_denied`` for «Medlem» and «Leser». 404 ``company_not_found``
    for a company the caller cannot open. No key may read it (403
    ``not_for_agent_keys``): who is being let in is access management.
    """
    require_permission(context.membership.role, Permission.MEMBER_MANAGE)
    invitations = await service.list_invitations(db, context.company.id)
    return [_invitation_response(invitation) for invitation in invitations]


@router.post("/companies/{company_id}/invitations", status_code=status.HTTP_201_CREATED)
async def invite_member(
    payload: InviteRequest,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    post: OutgoingMail,
) -> InvitationResponse:
    """Invite an email address to these books, at the given role.

    This is how a set of books ends up being kept by more than one person. The
    invitation is emailed to the address, and the response says whether that
    succeeded and carries the acceptance link exactly once, so an inviter whose
    deployment sends no mail — or whose message bounced — can still pass the
    link on.

    The send happens inline rather than in a background task. It takes a second
    against a working relay, and the alternative is an interface that claims a
    colleague was emailed before anything has tried: the one fact the inviter
    needs is precisely whether the message went.
    """
    invitation, token = await service.invite_member(
        db,
        company=context.company,
        actor=context.membership,
        email=payload.email,
        role=payload.role,
        full_name=payload.full_name,
    )
    delivery = await _post_invitation(
        post, invitation, token=token, company=context.company.name, sender=user
    )
    return _invitation_response(
        invitation, token=token, email_sent=delivery.sent, inviter=user.full_name
    )


@router.post("/companies/{company_id}/invitations/{invitation_id}/send-again")
async def resend_invitation(
    invitation_id: int,
    context: CurrentCompany,
    user: CurrentUser,
    db: DbSession,
    post: OutgoingMail,
) -> InvitationResponse:
    """Send a pending invitation again, with a new link.

    The gap this closes: only the token's hash is stored, so an invitation that
    landed in a spam folder could not be re-sent: the only way forward was to
    revoke it and retype the address, losing the role and the name with it. Now
    the same row gets a fresh token and a fresh deadline.

    The previous link stops working, which is not a side effect to hide: the
    message says so, because a recipient holding two emails needs to know which
    one is live.

    200 rather than 201: nothing is created. The response carries the new link
    exactly once, on the same terms as the original.
    """
    invitation, token = await service.resend_invitation(
        db,
        company_id=context.company.id,
        invitation_id=invitation_id,
        actor=context.membership,
    )
    delivery = await _post_invitation(
        post,
        invitation,
        token=token,
        company=context.company.name,
        sender=user,
        resent=True,
    )
    return _invitation_response(
        invitation, token=token, email_sent=delivery.sent, inviter=user.full_name
    )


async def _post_invitation(
    post: OutgoingMail,
    invitation: OrganizationInvitation,
    *,
    token: str,
    company: str,
    sender: CurrentUser,
    resent: bool = False,
) -> Delivery:
    """Compose and send one invitation message.

    Shared by the first send and every one after it, so a change to what the
    message says cannot reach one path and miss the other, which is exactly how
    a resend ends up being the only mail with no logo on it.
    """
    return await post.send(
        invitation_mail(
            to=invitation.email,
            company=company,
            role=invitation.role,
            invite_url=_invite_url(token),
            expires_at=as_utc(invitation.expires_at),
            inviter_name=sender.full_name,
            # Reply-To, so an invitee who is unsure the message is genuine
            # answers the colleague who sent it rather than a mailbox nobody
            # reads. Only once the sender has confirmed that address: anybody
            # can sign up as somebody else's address, and a message from our
            # domain must not route a stranger's reply to an address nobody has
            # shown is theirs. Unconfirmed, the reply goes to MAIL_REPLY_TO.
            inviter_email=sender.email if sender.email_verified else None,
            recipient_name=invitation.full_name,
            resent=resent,
        )
    )


@router.delete(
    "/companies/{company_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation(invitation_id: int, context: CurrentCompany, db: DbSession) -> None:
    """Withdraw an invitation nobody has accepted yet («Trekk tilbake»).

    The link in the mail stops working at once: whoever opens it is told the
    invitation is invalid or expired (400 ``invalid_invitation``). Nothing is
    sent to the invited address. 204 with no body.

    An invitation already accepted is not withdrawn here: the person has
    access by then, and it is taken away with
    ``DELETE /api/v1/companies/{company_id}/members/{user_id}``.

    Owners and administrators only (``member:manage``); 403
    ``permission_denied`` otherwise. 404 ``invitation_not_found`` for an id
    that is not a pending invitation to these books: accepted, withdrawn
    already, or another company's, all the same answer. 404
    ``company_not_found`` for a company the caller cannot open. No key may
    call it (403 ``not_for_agent_keys``): who is let in is access management.
    """
    require_permission(context.membership.role, Permission.MEMBER_MANAGE)
    await service.revoke_invitation(db, company_id=context.company.id, invitation_id=invitation_id)


@router.patch("/companies/{company_id}/members/{user_id}")
async def change_member_role(
    user_id: int, payload: ChangeRoleRequest, context: CurrentCompany, db: DbSession
) -> MemberResponse:
    """Change what somebody may do in these books: the role in the member list.

    ``role`` is ``owner`` («Eier»), ``admin`` («Administrator»), ``member``
    («Medlem») or ``viewer`` («Leser»); the member list
    (``GET /api/v1/companies/{company_id}/members``) says what each may do.
    It holds from the person's next request, and for their keys with it:
    a key never does more than its person's role allows on that request, so
    a role taken away needs no key stopped. Answers with the person's row as
    it now is.

    Owners and administrators only (``member:manage``), with two limits that
    keep the owner role worth having: only an owner may make somebody an
    owner, and only an owner may change an owner's role. Either is 403
    ``permission_denied``, as is a caller without ``member:manage``. 409
    ``last_owner`` when the change would leave the books with no owner:
    appoint another owner first. 404 ``not_a_member`` for a ``user_id`` with
    no access to these books, and 404 ``company_not_found`` for a company the
    caller cannot open. No key may call it (403 ``not_for_agent_keys``):
    managing access is never a key's (docs/agents-and-api.md rule 5).
    """
    target = await service.get_company_membership(
        db, company_id=context.company.id, user_id=user_id
    )
    await service.change_member_role(db, actor=context.membership, target=target, role=payload.role)
    await db.refresh(target, attribute_names=["user"])
    await db.refresh(target.user, attribute_names=["government_identity"])
    return _member_response(target, is_self=target.user_id == context.membership.user_id)


@router.delete("/companies/{company_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(user_id: int, context: CurrentCompany, db: DbSession) -> None:
    """Take away someone's access to these books, or give up your own.

    Passing your own user id is how a person walks away from a client's books,
    which needs no permission. Doing it to somebody else needs
    ``member:manage``, and neither may remove the last owner.

    Every key the person made for these books stops with the membership
    (docs/agents-and-api.md rule 3). A key would stop working anyway, since it
    acts with a membership that is gone, but stopped rather than idle means
    that inviting the person back later does not wake their old keys with them.
    """
    target = await service.get_company_membership(
        db, company_id=context.company.id, user_id=user_id
    )
    await service.remove_member(db, actor=context.membership, target=target)
    await agent_keys.stop_keys(
        db,
        reason=AgentKeyStop.MEMBER_REMOVED,
        user_id=target.user_id,
        company_id=context.company.id,
    )
