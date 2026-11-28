"""Deterministic ``@myntos.no`` inbox address generation and sender allowlist matching."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Sequence
from email.utils import parseaddr

from myntos.domain.mottak.errors import InvalidAllowedSenderError

# System, infrastructure, brand and Norwegian corporate role addresses that
# must never be assigned to a customer company.
RESERVED_LOCAL_PARTS: frozenset[str] = frozenset(
    {
        "abuse",
        "admin",
        "administrator",
        "api",
        "auth",
        "bilag",
        "billing",
        "bokforing",
        "conduct",
        "contact",
        "dmarc",
        "dns",
        "dpo",
        "efaktura",
        "epost",
        "faktura",
        "finance",
        "gdpr",
        "hei",
        "hello",
        "help",
        "hjelp",
        "hostmaster",
        "info",
        "innkommende",
        "invoice",
        "invoices",
        "kontakt",
        "legal",
        "mail",
        "mailer-daemon",
        "marketing",
        "mottak",
        "mottatt",
        "myntos",
        "no-reply",
        "noreply",
        "ops",
        "owner",
        "personvern",
        "post",
        "postmaster",
        "privacy",
        "regnskap",
        "root",
        "sales",
        "salg",
        "security",
        "sikkerhet",
        "smtp",
        "spam",
        "support",
        "sysadmin",
        "system",
        "team",
        "tech",
        "tls",
        "webmaster",
        "www",
    }
)

# Norwegian organisation forms stripped to build the preferred short slug
# (e.g. "Test AS" -> "test", then "testas" if "test" is taken).
_LEGAL_FORMS: frozenset[str] = frozenset(
    {
        "al",
        "ans",
        "as",
        "asa",
        "ba",
        "bbl",
        "bl",
        "brl",
        "da",
        "enk",
        "fli",
        "gfs",
        "iks",
        "kf",
        "ks",
        "ktrf",
        "nuf",
        "orgl",
        "pk",
        "pre",
        "sa",
        "sam",
        "se",
        "sf",
        "spa",
        "sti",
        "utla",
    }
)

_NORDIC_TRANSLATION = str.maketrans({"æ": "ae", "ø": "o", "å": "a"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
_MAX_BASE_LENGTH = 56
_MAX_LOCAL_LENGTH = 64
_LOCAL_PART_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}$")
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_EMAIL_LOCAL_RE = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}$")


def _ascii_lower(value: str) -> str:
    folded = value.casefold().translate(_NORDIC_TRANSLATION)
    decomposed = unicodedata.normalize("NFKD", folded)
    return decomposed.encode("ascii", "ignore").decode("ascii")


def _slug_words(name: str) -> list[str]:
    return [word for word in _WORD_SPLIT.split(_ascii_lower(name)) if word]


def candidate_local_parts(
    name: str,
    orgnr: str,
    *,
    extra_reserved: frozenset[str] = frozenset(),
) -> Iterator[str]:
    """Yield unique, valid, non-reserved local-part candidates in preference order.

    Examples:
    - ``"Test AS"`` -> ``"test"``, ``"testas"``, ``"testas2"``, ``"testas3"``, ...
    - ``"Kontakt AS"`` -> skips reserved ``"kontakt"`` -> ``"kontaktas"``, ``"kontaktas2"``, ...
    - ``"Ås & Ødegård ENK"`` -> ``"asodegard"``, ``"asodegardenk"``, ...
    - Fallback if every name-based candidate collides -> ``"org<orgnr>"``, ``"org<orgnr>2"``, ...
    """
    reserved = RESERVED_LOCAL_PARTS | extra_reserved
    words = _slug_words(name)
    stem_words = list(words)
    while len(stem_words) > 1 and stem_words[-1] in _LEGAL_FORMS:
        stem_words.pop()

    stem = "".join(stem_words)[:_MAX_BASE_LENGTH]
    full = "".join(words)[:_MAX_BASE_LENGTH]
    org_digits = _NON_ALNUM.sub("", orgnr)[:16] or "selskap"
    fallback_base = f"org{org_digits}"[:_MAX_BASE_LENGTH]

    seen: set[str] = set()

    def _allowed(candidate: str) -> bool:
        return (
            2 <= len(candidate) <= _MAX_LOCAL_LENGTH
            and candidate not in reserved
            and candidate not in seen
        )

    for primary in (stem, full):
        if _allowed(primary):
            seen.add(primary)
            yield primary

    numbered_base = full if len(full) >= 2 else (stem if len(stem) >= 2 else fallback_base)
    for suffix in range(2, 1000):
        candidate = f"{numbered_base}{suffix}"
        if _allowed(candidate):
            seen.add(candidate)
            yield candidate

    if _allowed(fallback_base):
        seen.add(fallback_base)
        yield fallback_base
    for suffix in range(2, 1000):
        candidate = f"{fallback_base}{suffix}"
        if _allowed(candidate):
            seen.add(candidate)
            yield candidate


def format_inbox_address(local_part: str, domain: str) -> str:
    """Combine a stored ``local_part`` with the configured inbound domain."""
    return f"{local_part.strip().lower()}@{domain.strip().lower()}"


def extract_local_part(recipient: str, domain: str) -> str | None:
    """Return the normalised local-part when ``recipient`` targets ``domain``, else ``None``.

    Supports optional ``+tag`` subaddressing (``test+faktura@myntos.no`` -> ``test``).
    """
    _, addr = parseaddr(recipient)
    cleaned = (addr or recipient).strip().lower()
    if "@" not in cleaned:
        return None
    local, _, host = cleaned.partition("@")
    if host != domain.strip().lower():
        return None
    if not local or not _LOCAL_PART_RE.match(local):
        return None
    base_local = local.split("+", 1)[0]
    return base_local or None


def email_domain(address: str | None) -> str | None:
    """Extract the lowercased domain from an email address, or ``None``."""
    if not address or "@" not in address:
        return None
    _, _, host = address.strip().lower().rpartition("@")
    return host or None


def normalise_allowed_sender(entry: str) -> str:
    """Validate and normalise one approved-sender rule (``user@domain.no`` or ``@domain.no``)."""
    cleaned = entry.strip().lower()
    if not cleaned or len(cleaned) > 320 or any(ch.isspace() for ch in cleaned):
        raise InvalidAllowedSenderError(
            f"ugyldig avsender «{entry.strip()}»: oppgi enten en e-postadresse "
            "(f.eks. faktura@leverandor.no) eller et domene (f.eks. @leverandor.no)"
        )
    if cleaned.startswith("@"):
        host = cleaned[1:]
        if not _DOMAIN_RE.match(host):
            raise InvalidAllowedSenderError(
                f"ugyldig domene «{entry.strip()}»: bruk formatet @eksempel.no"
            )
        return f"@{host}"

    local, sep, host = cleaned.partition("@")
    if (
        not sep
        or "@" in host
        or not _EMAIL_LOCAL_RE.match(local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or not _DOMAIN_RE.match(host)
    ):
        raise InvalidAllowedSenderError(
            f"ugyldig e-postadresse «{entry.strip()}»: oppgi enten en full e-postadresse "
            "eller et domene med @ foran (f.eks. @leverandor.no)"
        )
    return f"{local}@{host}"


def sender_matches_allowlist(sender_email: str, allowed_senders: Sequence[str]) -> bool:
    """Whether ``sender_email`` matches any exact address or ``@domain`` rule."""
    normalised = sender_email.strip().lower()
    if not normalised or "@" not in normalised:
        return False
    for raw_rule in allowed_senders:
        rule = raw_rule.strip().lower()
        if not rule:
            continue
        if rule.startswith("@"):
            if normalised.endswith(rule):
                return True
        elif normalised == rule:
            return True
    return False
