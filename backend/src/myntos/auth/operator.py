"""The operator key: the one credential that opens the operator's page, ``/drift/``.

Behind it is everything Myntos keeps that belongs to no company: every
stranger's feedback, in their own words, and the server's recorded failures
(docs/operator.md). That is not a set of books, so no membership, role or
session can open it, and no agent key either: a key belongs to one company.
It is one secret per deployment, held by whoever runs it.

Stored like every other credential in Myntos, as a SHA-256 digest:
``OPERATOR_KEY_SHA256`` in ``.env``. The key itself is 256 random bits behind
a prefix, so it cannot be guessed, needs no lockout, and a secret scanner (or a
person) recognises it on sight; it lives in the operator's password manager and
nowhere on the machine. Unset, the page and its routes answer 404, which is
what a deployment that never set it up, a test run and CI all get.

    uv run python -m myntos.auth.operator     # a new key, and the line for .env

A new key replaces the old one as soon as the backend restarts with the new
digest; there is nothing else to revoke.
"""

from __future__ import annotations

import secrets

from myntos.auth.tokens import hash_token, tokens_equal

KEY_PREFIX = "myntos_op_"


class OperatorError(Exception):
    """Base class for every refusal on the operator's routes."""


class OperatorPageOffError(OperatorError):
    """This deployment has no operator key, so there is no operator's page to open."""


class NotTheOperatorError(OperatorError):
    """The request carried no operator key, or the wrong one."""


def new_key() -> tuple[str, str]:
    """A fresh key, and the digest that goes in ``OPERATOR_KEY_SHA256``."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    return key, hash_token(key)


def is_operator_key(presented: str, digest: str) -> bool:
    """Whether ``presented`` is the key ``digest`` was made from, in constant time."""
    return tokens_equal(hash_token(presented), digest)


def main() -> int:
    key, digest = new_key()
    print(
        "The operator key. Keep it in your password manager; it is not stored anywhere:\n\n"
        f"    {key}\n\n"
        "Put this line in .env (it holds only the digest), then restart the backend:\n\n"
        f"    OPERATOR_KEY_SHA256={digest}\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
