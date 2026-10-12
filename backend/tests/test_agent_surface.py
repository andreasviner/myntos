"""Every route arrives with the half an agent needs, or the suite fails.

docs/agents-and-api.md rule 2: a feature is not done until every route it adds
is described in the OpenAPI document for a reader who does not know Norwegian
accounting, a skill under ``docs/agents/skills/`` teaches an agent to use it,
and ``llms.txt`` lists that skill. A rule like that lives in a document nobody
opens while building something else, and the feature that forgets it will be
the one built by someone with no reason to think about agents that day. So it
lives here too, where forgetting it is a red test rather than a gap found a
year later by a customer whose agent cannot do what the page can.

The routes are read out of the OpenAPI schema, for the reason
``test_company_boundary.py`` gives: a hand-written list is a list of what
somebody remembered.

This test was written on 2026-10-10, before the keys, ``llms.txt`` or a single
skill existed, and 43 of the 119 routes then had no description. Those routes
stand on the two lists below, and **the lists only shrink**. A route that gets
its description or its skill must leave the list in the same change (a test
fails until it does, so the list cannot rot into a set of stale excuses), and
**a new route never goes on them**: a new route is described and taught, or it
is put in ``_NOT_FOR_AGENTS`` with the reason no key may ever call it. Emptying
the lists is step 2 of the build plan in agents-and-api.md.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any

from myntos.api.deps import (
    KEY_APPROVAL_LABEL_EXTENSION,
    KEY_PREVIEW_EXTENSION,
    KEY_SCOPE_EXTENSION,
)
from myntos.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "docs" / "agents"
SKILLS_DIR = AGENTS_DIR / "skills"
HELP_DIR = AGENTS_DIR / "help"
LLMS_TXT = AGENTS_DIR / "llms.txt"
SOURCE_DIR = REPO_ROOT / "backend" / "src"
PAGES_DIR = REPO_ROOT / "frontend" / "src" / "pages"
PUBLISHED = "https://myntos.no/llms/"

_METHODS = {"get", "post", "put", "patch", "delete"}

# How a skill names a route: the method and the path exactly as the OpenAPI
# document spells it, parameters and all ("POST /api/v1/companies/{company_id}/
# faktura"). Exact, so that a renamed path parameter is a skill that no longer
# matches rather than a skill that quietly still looks right.
_ROUTE_IN_TEXT = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE) (/api/v1/[^\s`'\")\]?#]+)")

# Agent Skills format: lowercase letters, digits and hyphens, at most 64.
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Why a route is closed to every key. Each has to be rule 5 of
# agents-and-api.md (filing, money, access, deleting the account or a company,
# the token vault) or something as hard. "Agents would not need this" is not a
# reason; developers building their own frontend use the same API.
_SIGN_IN = (
    "A key is not a way into Myntos (agents-and-api.md rule 3): signing in, signing up and "
    "the links mailed for them belong to the person, in a browser."
)
_ACCOUNT = (
    "The person's own account: its sessions, its ways in, deleting it. Managing access and "
    "deleting the account are never a key's (rule 5)."
)
_ACCESS = (
    "Who may open these books, keys included. Managing access is never a key's (rule 5), "
    "and neither is reading who has it."
)
_MONEY = (
    "Where the company is paid. One changed kontonummer and the next invoice pays somebody "
    "else, which is what a stolen key would be used for (rule 5)."
)
_GOVERNMENT = (
    "The person's own ID-porten login and what Skatteetaten and Altinn are told. Filing and "
    "acting for a company towards the state are the person's, by Skatteetaten's policy "
    "(rule 5)."
)
_ONE_COMPANY = (
    "Above the one company a key belongs to: the account that owns companies is never shown, "
    "and a key sees one set of books (rule 3); GET /api/v1/agent/me names it."
)
_APPROVING = (
    "Approving or declining what a key asked is the person's yes or no (rule 13). A key that "
    "could approve requests would make every key a key that may do everything."
)
_COMPANY_SWITCHES = (
    "Carries ki_enabled, whether the company's bilag may reach a model at all, which is the "
    "company's decision about its tools and not one its tools may make (CLAUDE.md rule 5)."
)

# Routes no key may ever call, so no skill teaches them, each with its reason.
# Every route is here or declares a key scope (myntos.api.deps.KEY_READ and its
# siblings): see test_every_route_has_decided_about_keys.
_NOT_FOR_AGENTS: dict[str, str] = {
    "GET /api/v1/account/erasure": _ACCOUNT,
    "POST /api/v1/account/erasure": _ACCOUNT,
    "GET /api/v1/account/identities": _ACCOUNT,
    "DELETE /api/v1/account/identities/{provider}": _ACCOUNT,
    "GET /api/v1/account/sessions": _ACCOUNT,
    "POST /api/v1/account/sessions/revoke-others": _ACCOUNT,
    "DELETE /api/v1/account/sessions/{session_id}": _ACCOUNT,
    "POST /api/v1/account/switch-to-email": _ACCOUNT,
    "POST /api/v1/auth/bankid/link": _SIGN_IN,
    "POST /api/v1/auth/email/verification": _SIGN_IN,
    "POST /api/v1/auth/email/verify": _SIGN_IN,
    "POST /api/v1/auth/invitations/accept": _SIGN_IN,
    "POST /api/v1/auth/invitations/lookup": _SIGN_IN,
    "POST /api/v1/auth/login": _SIGN_IN,
    "POST /api/v1/auth/logout": _SIGN_IN,
    "GET /api/v1/auth/me": _SIGN_IN,
    "POST /api/v1/auth/password": _SIGN_IN,
    "POST /api/v1/auth/password/forgot": _SIGN_IN,
    "POST /api/v1/auth/password/reset": _SIGN_IN,
    "POST /api/v1/auth/provider-signup": _SIGN_IN,
    "POST /api/v1/auth/provider-signup/lookup": _SIGN_IN,
    "GET /api/v1/auth/providers": _SIGN_IN,
    "POST /api/v1/auth/register": _SIGN_IN,
    "POST /api/v1/auth/switch-to-email/confirm": _SIGN_IN,
    "GET /api/v1/auth/{provider}/callback": _SIGN_IN,
    "GET /api/v1/auth/{provider}/start": _SIGN_IN,
    "GET /api/v1/companies": _ONE_COMPANY,
    "POST /api/v1/companies": _ONE_COMPANY,
    "GET /api/v1/organizations": _ONE_COMPANY,
    "POST /api/v1/organizations": _ONE_COMPANY,
    "GET /api/v1/organizations/{organization_id}": _ONE_COMPANY,
    "PATCH /api/v1/organizations/{organization_id}": _ONE_COMPANY,
    "PATCH /api/v1/companies/{company_id}": _COMPANY_SWITCHES,
    "GET /api/v1/companies/{company_id}/agent-keys": _ACCESS,
    "POST /api/v1/companies/{company_id}/agent-keys": _ACCESS,
    "DELETE /api/v1/companies/{company_id}/agent-keys/{key_id}": _ACCESS,
    "PUT /api/v1/companies/{company_id}/agent-access": _ACCESS,
    "GET /api/v1/companies/{company_id}/members": _ACCESS,
    "PATCH /api/v1/companies/{company_id}/members/{user_id}": _ACCESS,
    "DELETE /api/v1/companies/{company_id}/members/{user_id}": _ACCESS,
    "GET /api/v1/companies/{company_id}/invitations": _ACCESS,
    "POST /api/v1/companies/{company_id}/invitations": _ACCESS,
    "DELETE /api/v1/companies/{company_id}/invitations/{invitation_id}": _ACCESS,
    "POST /api/v1/companies/{company_id}/invitations/{invitation_id}/send-again": _ACCESS,
    "GET /api/v1/companies/{company_id}/register": _ACCESS,
    "POST /api/v1/companies/{company_id}/godkjenninger/{approval_id}/godkjenn": _APPROVING,
    "POST /api/v1/companies/{company_id}/godkjenninger/{approval_id}/avvis": _APPROVING,
    "POST /api/v1/companies/{company_id}/bankkontoer": _MONEY,
    "PATCH /api/v1/companies/{company_id}/bankkontoer/{bank_account_id}": _MONEY,
    "DELETE /api/v1/companies/{company_id}/bankkontoer/{bank_account_id}": _MONEY,
    "POST /api/v1/companies/{company_id}/bekreft": _GOVERNMENT,
    "POST /api/v1/companies/{company_id}/mva/termin/kontroll": _GOVERNMENT,
    "POST /api/v1/companies/{company_id}/mva/innsendinger/{filing_id}/send": _GOVERNMENT,
    "POST /api/v1/companies/{company_id}/mva/innsendinger/{filing_id}/tilbakemelding": (
        _GOVERNMENT
    ),
    "GET /api/v1/skatt/altinn/authorized-parties": _GOVERNMENT,
    "GET /api/v1/skatt/idporten/callback": _GOVERNMENT,
    "GET /api/v1/skatt/idporten/start": _GOVERNMENT,
    "GET /api/v1/skatt/status": _GOVERNMENT,
}

# Routes that existed without an OpenAPI description on 2026-10-10. Closed.
_UNDESCRIBED_BEFORE_THE_RULE = frozenset(
    {
        "DELETE /api/v1/companies/{company_id}/invitations/{invitation_id}",
        "GET /api/v1/auth/me",
        "GET /api/v1/companies/{company_id}/invitations",
        "GET /api/v1/companies/{company_id}/members",
        "GET /api/v1/organizations/{organization_id}",
        "PATCH /api/v1/companies/{company_id}/members/{user_id}",
        "PATCH /api/v1/organizations/{organization_id}",
        "POST /api/v1/auth/login",
        "POST /api/v1/auth/logout",
    }
)

# Every route that existed on 2026-10-10, when there were no skills. Closed.
_UNTAUGHT_BEFORE_THE_RULE = frozenset[str]()


# --- What there is ---------------------------------------------------------------


@cache
def _schema_operations() -> dict[str, dict[str, Any]]:
    """``"METHOD /path"`` to its OpenAPI operation, for every route."""
    schema = create_app().openapi()
    return {
        f"{method.upper()} {path}": operation
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        if method in _METHODS
    }


@cache
def _operations() -> dict[str, str]:
    """``"METHOD /path"`` to its OpenAPI description, for every route."""
    return {
        op: (operation.get("description") or "").strip()
        for op, operation in _schema_operations().items()
    }


@cache
def _key_scopes() -> dict[str, str | None]:
    """``"METHOD /path"`` to what it declares about keys: a scope, "none", or nothing."""
    return {
        op: operation.get(KEY_SCOPE_EXTENSION) for op, operation in _schema_operations().items()
    }


def _routes_named_in(text: str) -> set[str]:
    return {f"{method} {path.rstrip('.,;:')}" for method, path in _ROUTE_IN_TEXT.findall(text)}


def _skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md")) if SKILLS_DIR.is_dir() else []


def _frontmatter(text: str) -> dict[str, str]:
    """The ``key: value`` lines between the opening ``---`` pair.

    Deliberately not YAML: the two fields a skill needs are one line each, and
    a parser this small refusing a folded description is a feature, because
    some agents read the frontmatter with something equally naive.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t")):
            fields[key.strip()] = value.strip()
    return {}


@cache
def _taught() -> frozenset[str]:
    names: set[str] = set()
    for skill in _skill_files():
        names |= _routes_named_in(skill.read_text(encoding="utf-8"))
    return frozenset(names)


# --- Guards on the guard ---------------------------------------------------------


def test_the_schema_is_read() -> None:
    """If the schema ever came back empty, every check below would pass on nothing."""
    assert len(_operations()) > 100


def test_a_route_in_a_skill_is_recognised() -> None:
    """The pattern that finds routes in a skill, against the way skills write them.

    A pattern that silently matched nothing would make every route look
    untaught and every skill look harmless, and the failure would read as a
    missing skill rather than a broken test.
    """
    text = (
        "1. Call `POST /api/v1/companies/{company_id}/faktura` with the lines.\n"
        "2. Then GET /api/v1/companies/{company_id}/faktura/{document_id}/kontroll.\n"
        "Not this: GET /something/else, nor GETTING /api/v1/x."
    )
    assert _routes_named_in(text) == {
        "POST /api/v1/companies/{company_id}/faktura",
        "GET /api/v1/companies/{company_id}/faktura/{document_id}/kontroll",
    }


# --- Rule 1: one API, and it is public -------------------------------------------


def test_no_route_is_hidden_from_the_schema() -> None:
    """A route left out of the OpenAPI document is a frontend-only surface.

    That is the private API rule 1 forbids, and it would also walk past every
    check in this file and in both company-boundary tests, which read the same
    document. Searched in the source because the routers are mounted lazily
    and the application object does not hold them flat.
    """
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in SOURCE_DIR.rglob("*.py")
        if "include_in_schema" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"Routes hidden from /api/v1/openapi.json in: {offenders}"


# --- Rule 2: described -----------------------------------------------------------


def test_every_route_has_a_description() -> None:
    """A route's docstring is its OpenAPI description: what agents and developers read.

    Write it for someone who has never kept Norwegian books: what the route
    does, when to call it, what it refuses and why, and the bokmål word the
    user will say for it.
    """
    missing = sorted(
        op
        for op, description in _operations().items()
        if not description and op not in _UNDESCRIBED_BEFORE_THE_RULE
    )
    assert not missing, (
        "These routes have no OpenAPI description (docs/agents-and-api.md rule 2). "
        "Give the route function a docstring. Do not add them to "
        f"_UNDESCRIBED_BEFORE_THE_RULE: that list is closed. {missing}"
    )


def test_the_undescribed_list_only_shrinks() -> None:
    operations = _operations()
    stale = sorted(
        op for op in _UNDESCRIBED_BEFORE_THE_RULE if op not in operations or operations[op]
    )
    assert not stale, (
        "These routes are described now, or gone. Take them off "
        f"_UNDESCRIBED_BEFORE_THE_RULE so the list keeps meaning something: {stale}"
    )


# --- Rule 2: taught --------------------------------------------------------------


def test_every_route_is_taught_or_refused() -> None:
    """A route is named in a skill, or listed as one no key may call, with the reason."""
    untaught = sorted(
        op
        for op in _operations()
        if op not in _taught() and op not in _NOT_FOR_AGENTS and op not in _UNTAUGHT_BEFORE_THE_RULE
    )
    assert not untaught, (
        "No skill in docs/agents/skills/ names these routes (docs/agents-and-api.md "
        "rule 2). Teach them in the feature's SKILL.md, written as `METHOD /path` exactly "
        "as the OpenAPI document spells it; or, if no key may ever call them (rule 5), "
        "add them to _NOT_FOR_AGENTS with the reason. Do not add them to "
        f"_UNTAUGHT_BEFORE_THE_RULE: that list is closed. {untaught}"
    )


def test_the_untaught_list_only_shrinks() -> None:
    operations = _operations()
    stale = sorted(
        op
        for op in _UNTAUGHT_BEFORE_THE_RULE
        if op not in operations or op in _taught() or op in _NOT_FOR_AGENTS
    )
    assert not stale, (
        "These routes are taught or refused now, or gone. Take them off "
        f"_UNTAUGHT_BEFORE_THE_RULE: {stale}"
    )


def test_refused_routes_exist_and_say_why() -> None:
    operations = _operations()
    scopes = _key_scopes()
    for op, reason in _NOT_FOR_AGENTS.items():
        assert op in operations, f"{op} is in _NOT_FOR_AGENTS but is not a route"
        assert len(reason.split()) >= 4, f"{op} needs a reason, not a label: {reason!r}"
        assert op not in _taught(), (
            f"{op} is refused to every key, yet a skill teaches it. One of them is wrong."
        )
        assert scopes[op] is None, (
            f"{op} is refused to every key here, yet declares {KEY_SCOPE_EXTENSION}: "
            f"{scopes[op]!r}. One of them is wrong."
        )


# --- Rule 5: every route has decided about keys -----------------------------------


def test_every_route_has_decided_about_keys() -> None:
    """Open to keys with a scope, public, or closed with a reason. Never undecided.

    The gate in myntos.api.deps refuses a key on any route that declares no
    scope, so a route nobody thought about is already safe. That is the floor,
    not the decision: a route closed by default is one an agent cannot use for
    no stated reason, and a feature its page can do and its agent cannot is
    half a feature (rule 1). So a new route says which it is.
    """
    scopes = _key_scopes()
    undecided = sorted(
        op for op, scope in scopes.items() if scope is None and op not in _NOT_FOR_AGENTS
    )
    assert not undecided, (
        "These routes neither declare a key scope nor say why no key may call them. Put "
        "openapi_extra=KEY_READ, key_write('…'), key_post('…') or KEY_PREVIEW on the route "
        "(myntos.api.deps), or "
        f"NO_KEY_NEEDED if it asks for no credential; or add it to _NOT_FOR_AGENTS with "
        f"the reason: {undecided}"
    )


def test_a_skill_teaches_only_what_a_key_can_call() -> None:
    """A skill that teaches a route a key is refused on sends every agent into a 403."""
    scopes = _key_scopes()
    closed = sorted(op for op in _taught() if op in scopes and scopes[op] is None)
    assert not closed, f"Skills teach routes no key may call: {closed}"


# --- The skills and the front door -----------------------------------------------


def _skill_problems(skill: Path) -> list[str]:
    text = skill.read_text(encoding="utf-8")
    fields = _frontmatter(text)
    folder = skill.parent.name
    problems = []
    if fields.get("name") != folder:
        problems.append(f"frontmatter name must be {folder!r}")
    if not _SKILL_NAME.match(folder) or len(folder) > 64:
        problems.append("the folder is not a skill name (lowercase, digits, hyphens, <= 64)")
    if not 20 <= len(fields.get("description", "")) <= 1024:
        problems.append("the description must say what it does and when to use it")
    named = _routes_named_in(text)
    if not named:
        problems.append("it names no route, so it teaches nothing this test can see")
    unknown = sorted(named - set(_operations()))
    if unknown:
        problems.append(f"it names routes that do not exist: {unknown}")
    return problems


def test_every_skill_is_well_formed() -> None:
    """The Agent Skills frontmatter, so Claude installs it and other models can read it.

    One test over every skill rather than one per skill, because a parametrize
    over an empty folder reports a skip, and a skip in a suite that is
    otherwise all green is the kind of noise people learn to read past.
    """
    problems = {
        str(skill.relative_to(REPO_ROOT)): found
        for skill in _skill_files()
        if (found := _skill_problems(skill))
    }
    assert not problems, problems


def test_llms_txt_lists_every_skill() -> None:
    """Rule 12: ``llms.txt`` is where an agent starts; a skill it does not list is never found."""
    skills = [path.parent.name for path in _skill_files()]
    if not skills:
        return
    assert LLMS_TXT.is_file(), f"There are skills but no {LLMS_TXT.relative_to(REPO_ROOT)}"
    index = LLMS_TXT.read_text(encoding="utf-8")
    unlisted = [name for name in skills if f"{PUBLISHED}skills/{name}.md" not in index]
    assert not unlisted, (
        f"llms.txt does not link these skills as {PUBLISHED}skills/<name>.md: {unlisted}"
    )


# --- Rule 13: a request that waits says what it asks ------------------------------


def test_every_writing_route_says_what_it_asks() -> None:
    """A key that may only ask to write has each request shown to its person, in bokmål.

    The label is the end of the sentence «Claude vil …» on /godkjenning/, and a
    request the person cannot read is one they approve blind or decline for no
    reason. So a route that writes declares one (``key_write("…")``,
    ``key_post("…")``), or says it stores nothing (``KEY_PREVIEW``) and so has
    nothing to approve.
    """
    unlabelled = sorted(
        op
        for op, operation in _schema_operations().items()
        if operation.get(KEY_SCOPE_EXTENSION) in {"write", "post"}
        and not operation.get(KEY_PREVIEW_EXTENSION)
        and not str(operation.get(KEY_APPROVAL_LABEL_EXTENSION) or "").strip()
    )
    assert not unlabelled, (
        "These routes write but do not say, in bokmål, what a person is asked to approve "
        "when a key's request to them waits (docs/agents-and-api.md rule 13). Declare them "
        f"with key_write('…') or key_post('…') from myntos.api.deps: {unlabelled}"
    )


# --- Rule 14: every page has its help ---------------------------------------------

# Pages no help article explains, each with the reason. A page a person uses
# to keep the books is never here: an AI asked how to do something on it has
# to find the answer (docs/agents/README.md, the help half).
_PAGES_WITHOUT_HELP: dict[str, str] = {
    "/about/": "About the company behind Myntos; nothing to do there.",
    "/ansvar/": "The disclaimer, read rather than used.",
    "/bankpriser/": "A public price comparison of Norwegian banks, read rather than used.",
    "/funksjoner/": "The public feature list, which describes what the help explains.",
    "/priser/": "The public price list; nothing to do there.",
    "/personvern/": "The privacy statement, read rather than used.",
    "/vilkar/": "The terms, read rather than used.",
}

_HELP_FIELDS = ("title", "description", "pages")


def _site_pages() -> set[str]:
    """Every page the frontend builds, as its path on the site, dynamic ones left out."""
    pages: set[str] = set()
    for page in PAGES_DIR.rglob("*.astro"):
        relative = page.relative_to(PAGES_DIR).with_suffix("")
        if any(part.startswith("[") for part in relative.parts):
            continue
        parts = [part for part in relative.parts if part != "index"]
        pages.add("/" + "".join(f"{part}/" for part in parts))
    return pages


def _help_files() -> list[Path]:
    return sorted(HELP_DIR.glob("*/*.md")) if HELP_DIR.is_dir() else []


def _help_problems(article: Path) -> list[str]:
    text = article.read_text(encoding="utf-8")
    fields = _frontmatter(text)
    problems = [
        f"no {field}: in the frontmatter" for field in _HELP_FIELDS if not fields.get(field)
    ]
    if not 20 <= len(fields.get("description", "")) <= 1024:
        problems.append("the description must say what it answers and when to use it")
    if not _SKILL_NAME.match(article.stem) or not _SKILL_NAME.match(article.parent.name):
        problems.append("the area and the file are lowercase words joined by hyphens")
    unknown_pages = sorted(set(fields.get("pages", "").split()) - _site_pages())
    if unknown_pages:
        problems.append(f"it names pages the site does not have: {unknown_pages}")
    skill = fields.get("skill")
    if skill and not (SKILLS_DIR / skill / "SKILL.md").is_file():
        problems.append(f"it names a skill that does not exist: {skill}")
    return problems


def test_every_help_article_is_well_formed() -> None:
    problems = {
        str(article.relative_to(REPO_ROOT)): found
        for article in _help_files()
        if (found := _help_problems(article))
    }
    assert not problems, problems


def test_every_page_has_a_help_article() -> None:
    """A page of the app with no article is a page an AI cannot help anybody use.

    Add an article under docs/agents/help/<area>/ whose ``pages:`` names the
    page, and list it in llms.txt. Only public pages with nothing to do on them
    may stand on ``_PAGES_WITHOUT_HELP``, with the reason.
    """
    explained: set[str] = set()
    for article in _help_files():
        explained |= set(_frontmatter(article.read_text(encoding="utf-8")).get("pages", "").split())
    missing = sorted(_site_pages() - explained - set(_PAGES_WITHOUT_HELP))
    assert not missing, (
        "No help article in docs/agents/help/ explains these pages (docs/agents-and-api.md "
        f"rule 14, docs/agents/README.md): {missing}"
    )
    stale = sorted(set(_PAGES_WITHOUT_HELP) - _site_pages())
    assert not stale, f"_PAGES_WITHOUT_HELP names pages that are gone: {stale}"


def test_llms_txt_lists_every_help_article() -> None:
    index = LLMS_TXT.read_text(encoding="utf-8")
    unlisted = [
        str(article.relative_to(HELP_DIR))
        for article in _help_files()
        if f"{PUBLISHED}help/{article.parent.name}/{article.name}" not in index
    ]
    assert not unlisted, f"llms.txt does not link these help articles: {unlisted}"


def test_every_link_into_the_published_docs_points_at_a_file() -> None:
    """A link in llms.txt, a skill or an article to a file that is not there is a dead end.

    The AI following it fetched this file from somewhere else and has nothing
    to go on but the link.
    """
    pattern = re.compile(re.escape(PUBLISHED) + r"(skills|help)/([a-z0-9/-]+)\.md")
    sources = [LLMS_TXT, *_skill_files(), *_help_files()]
    dead: dict[str, list[str]] = {}
    for source in sources:
        for half, name in pattern.findall(source.read_text(encoding="utf-8")):
            target = SKILLS_DIR / name / "SKILL.md" if half == "skills" else HELP_DIR / f"{name}.md"
            if not target.is_file():
                dead.setdefault(str(source.relative_to(REPO_ROOT)), []).append(f"{half}/{name}")
    assert not dead, dead
