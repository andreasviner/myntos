"""Print the public JWK to register with Digdir, or generate a new key pair.

Registering a client in Samarbeidsportalen means pasting a **public** JWK into
a form. The private half stays here; the portal never sees it, and neither does
anything that writes logs. This module exists because getting that one step
wrong is the single most common way the whole integration silently fails: the
``kid`` in the JWT header has to be the ``kid`` the portal recorded, and there
is no error message that says so — Maskinporten answers ``invalid_client`` and
leaves you guessing whether the key, the id or the environment is wrong.

    # what to register for the key already configured in .env
    uv run python -m myntos.auth.digdir_key show

    # a fresh 4096-bit key pair for a new client
    uv run python -m myntos.auth.digdir_key generate --out ../secrets/idporten.pem

Both print the public JWK and the ``kid``. ``generate`` writes the private key
as a PEM with owner-only permissions and prints the two environment variables
to set; it refuses to overwrite an existing file, because the file it would
overwrite may be the only copy of a key some registration depends on.

Registered keys **expire after at most one year** (Digdir's rule), so rotation
is a scheduled task and not an emergency: generate a second key, register it
alongside the first, switch the ``*_KID`` variable, then remove the old one from
the portal.
"""

from __future__ import annotations

import argparse
import json
import stat
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from myntos.auth.signing_keys import SigningKeyError, load_signing_key, public_jwk
from myntos.core.config import Settings, get_settings

# Digdir's floor is 2048. 4096 costs a few milliseconds per token request, once
# per token, and the key lives for a year — there is nothing to save here.
_KEY_SIZE = 4096

# ``bankid`` is not a Digdir client, but its key is read the same way and
# registered the same way, as public key material pasted into a dashboard
# (Idura's: the application's OpenID Connect settings, "Client JWKS"), so it is
# shown here rather than by a second tool that would say the same thing.
_CLIENTS = ("maskinporten", "idporten", "bankid")


def _key_path(settings: Settings, client: str) -> tuple[Path | None, str | None]:
    """The configured key file and ``kid`` for one client."""
    if client == "maskinporten":
        return settings.maskinporten_key_file, settings.maskinporten_kid
    if client == "bankid":
        return settings.bankid_key_file, settings.bankid_kid
    return settings.idporten_key_file, settings.idporten_kid


def _show(client: str) -> int:
    """Print what to register for a key that is already configured."""
    settings = get_settings()
    path, kid = _key_path(settings, client)
    if path is None:
        print(
            f"No key configured for {client}: set "
            f"{client.upper()}_JWK_PATH in .env first, or run "
            f"'python -m myntos.auth.digdir_key generate' to make one.",
            file=sys.stderr,
        )
        return 1

    try:
        # Loaded with the configured kid so this reports exactly what the token
        # requests will send — including the case where a PEM has no kid at all,
        # which is the failure this command exists to make visible.
        signing_key = load_signing_key(path, kid=kid)
    except SigningKeyError as error:
        print(f"{error}", file=sys.stderr)
        # A PEM without a kid still has a registrable public half, and printing
        # it is the whole point of being asked. The thumbprint is offered as the
        # kid to register precisely because it is derived from the key.
        return _show_unregistered(path)

    jwk = public_jwk(signing_key.key, kid=signing_key.kid)
    print(f"{client}: {path}")
    print(f"kid: {signing_key.kid}")
    if client == "bankid":
        # Idura's guide registers a JWK *set*, so that is what is printed.
        print("Public JWKS to register as the broker application's client JWKS:")
        print(json.dumps({"keys": [jwk]}, indent=2))
        return 0
    print("Public JWK to register in Samarbeidsportalen:")
    print(json.dumps(jwk, indent=2))
    return 0


def _show_unregistered(path: Path) -> int:
    """Print the public JWK for a key with no usable ``kid`` configured."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        key = load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError):
        return 1
    if not isinstance(key, rsa.RSAPrivateKey):
        return 1

    jwk = public_jwk(key)
    print()
    print("The key itself is fine. Register this public JWK, then set the")
    print(f"*_KID variable to {jwk['kid']}:")
    print(json.dumps(jwk, indent=2))
    return 1


def _generate(out: Path) -> int:
    """Write a new private key and print what to register and configure."""
    if out.exists():
        print(
            f"{out} already exists. Refusing to overwrite it — a key file may "
            "be the only copy of a key some registration depends on.",
            file=sys.stderr,
        )
        return 1

    key = rsa.generate_private_key(public_exponent=65537, key_size=_KEY_SIZE)
    pem = key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pem)
    # Owner-only. A no-op on Windows, where the directory's ACL is what governs,
    # and the reason ./secrets/ is gitignored rather than merely conventional.
    out.chmod(stat.S_IRUSR | stat.S_IWUSR)

    jwk = public_jwk(key)
    # The public half goes to a file of its own, next to the key. Copying a
    # 4096-bit modulus out of a terminal is how a truncated key gets registered,
    # and a truncated key is answered with the same `invalid_client` as a wrong
    # one. This file is safe to open, mail or paste anywhere: it is the public
    # half, and the private exponent is not in it.
    public_path = out.with_suffix(".public.jwk.json")
    public_path.write_text(json.dumps(jwk, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote a new {_KEY_SIZE}-bit private key to {out}")
    print(f"Wrote the public JWK to register to {public_path}")
    print()
    print(f"kid: {jwk['kid']}")
    print()
    print("Register the contents of that file in Samarbeidsportalen, then set")
    print("for a Maskinporten client:")
    print(f"  MASKINPORTEN_JWK_PATH={out}")
    print(f"  MASKINPORTEN_KID={jwk['kid']}")
    print("or the IDPORTEN_ or BANKID_ equivalents for those clients.")
    print()
    print("Read the kid back from the portal after registering: Digdir assigns")
    print("its own (a UUID) rather than keeping the one above, and the portal's")
    print("value is the one the JWT header has to carry.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m myntos.auth.digdir_key",
        description="Public JWKs for Digdir client registration.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    show = commands.add_parser("show", help="print the public JWK for a configured key")
    show.add_argument("client", nargs="?", default="maskinporten", choices=_CLIENTS)

    generate = commands.add_parser("generate", help="generate a new client key pair")
    generate.add_argument("--out", type=Path, required=True, help="where to write the PEM")

    args = parser.parse_args(argv)
    if args.command == "generate":
        return _generate(args.out)
    return _show(args.client)


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
