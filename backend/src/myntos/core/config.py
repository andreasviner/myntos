"""Application configuration.

All runtime configuration comes from environment variables (12-factor). In
development the repo-root ``.env`` file is read (see ``.env.example`` for the
documented list); in production, inject real environment variables and never
ship a ``.env`` file.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Later entries win, so a backend-local .env can override the repo root one.
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- General ---
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # --- Database ---
    database_url: str = "postgresql+asyncpg://myntos:myntos@localhost:5432/myntos"

    # --- CORS (JSON array in the env var) ---
    # Doubles as the trusted-origin allow-list for cookie-authenticated writes
    # (myntos.api.deps), so this is not only a browser-fetch concern.
    cors_origins: list[str] = ["http://localhost:4321"]

    # --- Application accounts (Myntos' own login) ---
    # Absolute URL of the frontend: post-login redirects and invitation links
    # are built from it, so it must be the origin a browser actually reaches.
    frontend_base_url: str = "http://localhost:4321"

    session_cookie_name: str = "myntos_session"
    # Set when the API and the frontend live on different subdomains of one site
    # (e.g. ".myntos.no"). Left unset the cookie is host-only, which is the
    # safer default.
    session_cookie_domain: str | None = None
    # Absolute cap: a session dies at this age however active it has been.
    session_lifetime_days: int = 14
    # Idle cap. A signed-in session can post journal entries and read salary
    # figures, so an unattended browser must not stay usable indefinitely.
    session_idle_timeout_hours: int = 24

    # Length only — see myntos.auth.passwords for why composition rules are not
    # used. 10 is above the NIST SP 800-63B minimum of 8, and short enough that
    # people reach for a passphrase rather than a saved-somewhere-else password.
    # Reported by GET /auth/providers so the sign-up form states the same rule.
    password_min_length: int = 10
    # Per-account throttle. Broad, source-based rate limiting belongs at the
    # edge (Cloudflare), not here.
    login_max_failed_attempts: int = 10
    login_lockout_minutes: int = 15
    invitation_lifetime_days: int = 7
    # How long a "confirm your address" link works. Two days rather than the
    # invitation's seven: nothing is lost when it dies, because the person it was
    # sent to is signed in and can ask for another with one click.
    email_verification_lifetime_hours: int = 48
    # How long a password-reset link works. Short on purpose: the link is a
    # credential sitting in an inbox, and whoever asked for it is waiting for it
    # right now. A link found in a mailbox tomorrow should already be dead.
    password_reset_lifetime_minutes: int = 30
    # Per account and per kind of message, in any rolling hour. The reset
    # request is anonymous, so without this anybody can make Myntos mail a
    # stranger as often as they can press a button; it is the mail-side twin of
    # the login lockout above, and like it, it bounds one known address rather
    # than a sender (that belongs at the edge).
    account_mail_max_per_hour: int = 3

    # --- Keys for agents and integrations (docs/agents-and-api.md) ---
    # What a person copies off the Tilgang page and pastes into a conversation
    # with their agent. It is good for one exchange and nothing else, and the
    # day is for the person who makes the key in the evening and connects the
    # agent the next morning; a pasted key that was never used dies on its own.
    agent_connection_key_lifetime_hours: int = 24
    # Short, because it is the one credential the agent sends on every request,
    # so it is the one most likely to end up in a log, a trace or a transcript.
    agent_access_token_lifetime_minutes: int = 15
    # A refresh chain nobody has used for this long is dead. An agent that is
    # in daily use never notices; one that was set up once and forgotten stops
    # being a standing way into the books.
    agent_refresh_idle_days: int = 30
    # And a key dies at this age however busy it is, so every key is re-issued,
    # and re-read by a person on the Tilgang page, at least once a year.
    agent_key_lifetime_days: int = 365
    # Per key. Generous for an agent doing real work, small next to what a key
    # copied into a script that loops would do. Counted in the process, which
    # is exact with the one uvicorn process this deployment runs; with more
    # workers it becomes a limit per worker.
    agent_key_requests_per_minute: int = 120
    # Refusals that only a confused or a stolen key produces (another
    # company's id, a route no key may call, a scope it was not given) within
    # ten minutes before the key is stopped. An agent that reads the
    # documentation hits a handful while it learns; a key being used to probe
    # hits this many in seconds.
    agent_key_refusals_before_suspension: int = 20
    # How long a request that waits for its person's approval keeps waiting.
    # Long enough for somebody who looks at Myntos once a week; short enough
    # that nobody approves, in March, a payment their agent proposed against
    # the books as they stood at Christmas. Expired requests are not run.
    agent_approval_lifetime_days: int = 14
    # The most a waiting request may carry, body and files together. Stored in
    # the database until it is decided, so it is bounded; a bilag is at most
    # 25 MB (MAX_ATTACHMENT_BYTES), and a request carrying more than one of
    # those should be several requests.
    agent_approval_max_bytes: int = 30 * 1024 * 1024

    # --- Feedback from anybody (docs/feedback.md) ---
    # Everything that arrives in a rolling hour, from everybody together. The
    # senders are anonymous, so there is nobody to count one at a time, and a
    # limit per source address belongs at the edge rather than here (see
    # ARCHITECTURE.md § Security model). This bounds what a flood can write.
    feedback_max_per_hour: int = 60
    # Items nobody has read yet. Past this, new feedback is refused until the
    # operator has read some: a table nobody reads is not worth filling.
    feedback_max_unread: int = 500
    # Who is told that something arrived; unset, nobody is, and the operator
    # reads the list on the machine. The mail never carries the text.
    feedback_notify_to: str | None = None
    # How long an item that is done or declined is kept before the daily job
    # deletes it. Long enough to be asked about again, short enough that what
    # strangers wrote is not kept for ever. Open items are kept until closed.
    feedback_retention_days: int = 365

    # --- The operator's page and the error log (docs/operator.md) ---
    # The SHA-256 digest of the operator key, which opens /drift/: every
    # stranger's feedback and the server's recorded failures. Only the digest
    # is here, as for every credential in Myntos; the key is made by
    # `python -m myntos.auth.operator` and kept by the operator. Unset, or not
    # a digest, the page's routes answer 404 (``operator_key_digest``).
    operator_key_sha256: str | None = None
    # How long a kind of failure is kept after it last happened.
    server_error_retention_days: int = 90

    # --- Outgoing email (optional; nothing below is required to run) ---
    #
    # Two transports, and a deployment picks one by configuring it. Left unset,
    # Myntos sends nothing at all and says so: an invitation still produces a
    # link for the inviter to pass on by hand, which is what happened before
    # there was any mail at all. Address confirmation and password reset are
    # simply not offered then, because both are a link only an inbox can carry.
    # A half-configured deployment is treated as unconfigured rather than as one
    # that fails on the first invitation.
    #
    # `mail_from` belongs to both, because the sender is a fact about the
    # product rather than about the wire.
    #
    # SMTP, first, because every provider speaks it and none of them have to be
    # trusted with the message before it leaves.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    # STARTTLS on the submission port is the ordinary case. Set SMTP_TLS_MODE to
    # "ssl" for implicit TLS (port 465), or to "none" for a local relay on
    # 127.0.0.1 that does not offer TLS at all. Never "none" over a network.
    smtp_tls_mode: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_timeout_seconds: float = 15.0
    # The envelope sender and what a recipient sees. `mail_from` must be an
    # address the SMTP host is allowed to send as, or the message is rejected
    # (or, worse, delivered to spam) however correct everything else is.
    mail_from: str | None = None
    mail_from_name: str = "Myntos"
    # Where a reply goes. Unset, replies go to `mail_from`, which may be a
    # no-reply mailbox nobody reads.
    mail_reply_to: str | None = None

    # Cloudflare Email Sending, second, for a deployment whose domain is on
    # Cloudflare already: the DNS the deliverability of an invitation actually
    # rests on (SPF, DKIM, DMARC) is then written by the same party that accepts
    # the message, so there is no set of records to keep in step by hand.
    #
    # The account is the one R2 uses; it is named separately rather than read
    # from `r2_account_id` because storage and mail are configured
    # independently, and a deployment that moves one should not silently move
    # the other. The token wants the "Send email" permission and nothing else.
    cloudflare_account_id: str | None = None
    cloudflare_email_token: SecretStr | None = None

    @property
    def smtp_configured(self) -> bool:
        """Whether SMTP has both halves it needs: somewhere to send, and as whom."""
        return bool(self.smtp_host) and bool(self.mail_from)

    @property
    def cloudflare_mail_configured(self) -> bool:
        """Whether Cloudflare Email Sending has an account, a token and a sender."""
        return (
            bool(self.cloudflare_account_id)
            and self.cloudflare_email_token is not None
            and bool(self.mail_from)
        )

    @property
    def mail_configured(self) -> bool:
        """Whether outgoing mail can actually be sent, by any transport.

        Never a partial answer: either one alone is a deployment that would fail
        at the first send. Everything that offers to send email asks this first
        and says plainly when the answer is no.
        """
        return self.smtp_configured or self.cloudflare_mail_configured

    # --- Federated sign-in (all optional; password login works without any) ---
    # A provider appears in the interface only when its three values are set,
    # so a half-configured one is absent rather than a button that fails.

    # Google Cloud Console → Credentials → OAuth client ID → "Web application".
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str | None = None
    google_well_known_url: str = "https://accounts.google.com/.well-known/openid-configuration"

    # Microsoft Entra ID (Azure AD). Register an application, add a Web
    # redirect URI, and create a client secret.
    microsoft_client_id: str | None = None
    microsoft_client_secret: SecretStr | None = None
    microsoft_redirect_uri: str | None = None
    # Which accounts may sign in: "common" (work, school and personal),
    # "organizations", "consumers", or a tenant GUID to allow only your own.
    # This is an authorization boundary, not a formality — with "common", any
    # Microsoft tenant in the world can start a sign-in.
    microsoft_tenant: str = "common"
    # Derived from the tenant unless set explicitly.
    microsoft_well_known_url: str | None = None

    # BankID, through any OpenID Connect broker; Idura (formerly Criipto, part of
    # Stø, which runs BankID) is the documented one. docs/ARCHITECTURE.md,
    # "Federated providers", has the values and the pages they were read from.
    # Offered only when the issuer, the client id, the redirect URI and one
    # client credential are all set. This is never ID-porten: Digdir's terms do
    # not allow ID-porten as a private service's own login.
    #
    # The broker's issuer, e.g. https://myntos.test.idura.broker in Idura's
    # sandbox. The discovery document is read from
    # {issuer}/.well-known/openid-configuration, as OIDC Discovery 1.0 § 4
    # defines it. A bare domain is taken to mean https://{domain}, because that
    # is how Idura's dashboard shows it.
    bankid_issuer: str | None = None
    bankid_client_id: str | None = None
    bankid_redirect_uri: str | None = None
    # Client authentication. ``private_key_jwt`` when a key is set, which Idura
    # recommends and supports (token_endpoint_auth_methods_supported lists
    # client_secret_basic, client_secret_post and private_key_jwt): the key is
    # read exactly like the ID-porten one (myntos.auth.signing_keys), and its
    # public half is registered as the application's client JWKS. A secret
    # works too, for a broker or an application set up with one.
    bankid_jwk_path: Path | None = None
    bankid_jwk: SecretStr | None = None
    bankid_kid: str | None = None
    bankid_client_secret: SecretStr | None = None
    # Which BankID. Idura's values: ``urn:grn:authn:no:bankid:substantial`` for
    # BankID Biometrics, the app (assurance level substantial), and
    # ``urn:grn:authn:no:bankid`` for the classic code-device authenticators
    # (level high). Idura's page also names ``…:bankid:high`` for those, but
    # its discovery document does not list it, so it is not the example here.
    # Substantial is the default: this is a login to a bookkeeping program, not
    # a signature, and every filing still demands its own ID-porten login at
    # the high level.
    bankid_acr_values: str = "urn:grn:authn:no:bankid:substantial"
    # Never more than identity. Idura releases the name with ``openid`` alone;
    # a broker that needs ``profile`` for it can have it here. Never ``ssn``:
    # see myntos.auth.oidc.providers.bankid_profile for why the fødselsnummer
    # is not asked for.
    bankid_scope: str = "openid"

    # --- Maskinporten (docs/integrations/maskinporten.md) ---
    maskinporten_client_id: str | None = None
    maskinporten_well_known_url: str = (
        "https://test.maskinporten.no/.well-known/oauth-authorization-server"
    )
    maskinporten_scopes: str = ""
    # The private key that signs the JWT grant. Named ``jwk`` for the variable
    # that was already documented, but either encoding is read: a JWK document
    # (``{"kty":"RSA",...}``, possibly wrapped in a ``{"keys":[...]}`` set) or a
    # PEM private key. See myntos.auth.maskinporten.keys.
    maskinporten_jwk_path: Path | None = None
    # The same key inline, for a deployment whose credentials arrive as secret
    # bindings rather than as files. Takes precedence over the path when both
    # are set, because a container with an injected secret has no file to read.
    maskinporten_jwk: SecretStr | None = None
    # The key id registered in Samarbeidsportalen. Only needed when the key file
    # is a PEM, which carries no ``kid`` of its own — a JWK's own ``kid`` wins.
    # Maskinporten looks the signing key up by this value, so a mismatch is an
    # ``invalid_client``, not a signature error.
    maskinporten_kid: str | None = None
    # Acting on a client's behalf (regnskapsfører): the orgnr of the company
    # whose delegation is being used, added to the grant as ``consumer_org``.
    # Normally left unset and passed per request instead — a deployment that
    # keeps books for several companies cannot have one global value.
    maskinporten_consumer_org: str | None = None

    # --- ID-porten (required for validating/submitting MVA-melding & skattemelding) ---
    idporten_client_id: str | None = None
    idporten_well_known_url: str = "https://test.idporten.no/.well-known/openid-configuration"
    idporten_redirect_uri: str | None = None
    # Scopes beyond ``openid``. These are ordered from Skatteetaten and are
    # inert until granted, so they are configuration rather than constants:
    # requesting one the client does not have fails the whole authorization.
    idporten_scopes: str = ""
    # ID-porten clients authenticate with ``private_key_jwt`` (Digdir's default
    # and recommendation) — the same key handling as Maskinporten, a separate
    # key because it is a separate client. A ``client_secret`` client works too;
    # set the secret instead and leave these unset.
    idporten_jwk_path: Path | None = None
    # The ID-porten key inline. See ``maskinporten_jwk``.
    idporten_jwk: SecretStr | None = None
    idporten_kid: str | None = None
    idporten_client_secret: SecretStr | None = None
    # Level of assurance demanded of the login. ``idporten-loa-high`` means
    # BankID/Buypass/Commfides rather than MinID — which is what Skatteetaten
    # requires for submissions, and what a signature legally attached to a
    # personnummer should demand anyway.
    idporten_acr_values: str = "idporten-loa-high"

    # Fernet key (urlsafe base64, 32 bytes) that a government access token is
    # encrypted with at rest. Unset, the government login is unavailable rather
    # than stored in the clear: the token authorizes filing a tax return on a
    # named person's behalf, and that is not something to keep in a readable
    # column because a key was inconvenient. Generate one with:
    #   python -c "from cryptography.fernet import Fernet; \
    #              print(Fernet.generate_key().decode())"
    government_token_key: str = ""

    # --- Altinn (token exchange + instance APIs) ---
    altinn_platform_base_url: str = "https://platform.tt02.altinn.no"
    # Altinn 3 apps, one absolute URL each: the host differs between
    # environments, which is why these are configuration and not constants. The
    # MVA app id is ``…-v1`` in test as well as production; ``…-etm2`` was once
    # documented for test and answers 418 on tt02. The org
    # prefix is part of the path — ``skd`` for Skatteetaten, ``brg`` for
    # Brønnøysundregistrene.
    # Test: https://skd.apps.tt02.altinn.no/skd/formueinntekt-skattemelding-v2
    # Prod: https://skd.apps.altinn.no/skd/formueinntekt-skattemelding-v2
    altinn_skattemelding_app_url: str | None = None
    # Test: https://skd.apps.tt02.altinn.no/skd/mva-melding-innsending-v1
    # Prod: https://skd.apps.altinn.no/skd/mva-melding-innsending-v1
    altinn_mva_app_url: str | None = None

    # --- Altinn systembruker (system vendor registration) ---
    # Our own organisasjonsnummer, as the *vendor* of this system. Not the same
    # as maskinporten_consumer_org, which is a customer we act for: this is who
    # offers the system. It forms the system id, so it cannot be derived from a
    # token at registration time.
    altinn_vendor_orgnr: str = ""
    # The vendor-chosen half of the system id, which Altinn requires to be
    # "{vendor orgnr}_{name}". Permanent in practice: once a customer has
    # approved a system user against an id, changing it means every customer
    # approving again.
    altinn_system_name: str = "myntos"
    # Where a customer's browser may return after approving a system user.
    # Altinn requires https, so a loopback development URL is not valid here
    # even in test.
    altinn_system_redirect_url: str = "https://myntos.no/skatt/systembruker/godkjent"

    # --- Skatteetaten ---
    # Three separate hosts, because Skatteetaten splits them by auth plane and
    # the test names do not follow one pattern (docs/integrations/).
    # Maskinporten plane — test: https://api-test.sits.no
    #                      prod: https://api.skatteetaten.no
    skatteetaten_api_base_url: str | None = None
    # ID-porten plane (skattemelding) — test: https://idporten-api-test.sits.no
    #                                   prod: https://idporten.api.skatteetaten.no
    skatteetaten_idporten_api_base_url: str | None = None
    # MVA validation — test: https://idporten-api-test.sits.no. Skatteetaten
    #                  documents idporten-api-sbstest.sits.no, which resets
    #                  every connection after the TLS handshake (mva.md,
    #                  Endpoints); the -test host answers the same path.
    #                  prod: https://idporten.api.skatteetaten.no
    skatteetaten_mva_api_base_url: str | None = None

    # --- Løynd (the model that reads an uploaded bilag) ---
    # Optional in the same sense as the sign-in providers: without a key the
    # model is simply not asked, and the register falls back to its own reading
    # of the document. Norwegian-hosted inference is the reason this particular
    # service is the one allowed to see a user's bilag — see
    # myntos.integrations.loynd.
    loynd_key: SecretStr | None = None
    loynd_base_url: str = "https://api.loynd.no"
    loynd_model: str = "loynd-lite"

    # --- Enhetsregisteret (Brønnøysundregistrene) ---
    # Open data, no credentials. Configurable only so tests can point it at a
    # stub instead of the real register.
    brreg_api_base_url: str = "https://data.brreg.no/enhetsregisteret/api"

    # --- Document storage for bilag ---
    storage_backend: Literal["local", "r2"] = "local"
    local_storage_path: Path = REPO_ROOT / "data" / "bilag"
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: SecretStr | None = None
    r2_bucket: str | None = None
    # Part of the endpoint hostname, not a preference: a bucket created with
    # jurisdiction "eu" lives in a separate namespace reachable only at
    # <account>.eu.r2.cloudflarestorage.com, and the jurisdiction cannot be
    # changed after creation. Defaulting to "eu" means keeping accounting
    # records inside the EEA is what happens unless someone opts out.
    r2_jurisdiction: Literal["eu", "fedramp", "default"] = "eu"
    # Full override of the S3 endpoint, for a self-hosted S3-compatible store or
    # a test double. Normally derived from the account id and jurisdiction.
    r2_endpoint_url: str | None = None
    # --- Working copies: uploads a job reads and then deletes ---
    # The previous program's SAF-T files and printouts, kept until the import is
    # posted or thrown away and never more than 14 days. A bucket of their own,
    # beside the bilag archive and with the same account, keys and
    # jurisdiction, because the archive must never delete and these must
    # (myntos.storage.working).
    r2_working_bucket: str | None = None
    local_working_path: Path = REPO_ROOT / "data" / "working"

    @property
    def maskinporten_key_file(self) -> Path | None:
        """The Maskinporten key file as an absolute path, or ``None``."""
        return _repo_relative(self.maskinporten_jwk_path)

    @property
    def maskinporten_key_material(self) -> bytes | None:
        """The Maskinporten key given inline, or ``None``."""
        return _key_material(self.maskinporten_jwk)

    @property
    def idporten_key_material(self) -> bytes | None:
        """The ID-porten key given inline, or ``None``."""
        return _key_material(self.idporten_jwk)

    @property
    def idporten_key_file(self) -> Path | None:
        """The ID-porten key file as an absolute path, or ``None``."""
        return _repo_relative(self.idporten_jwk_path)

    @field_validator("bankid_jwk_path", mode="before")
    @classmethod
    def _blank_path_is_unset(cls, value: object) -> object:
        """Read ``BANKID_JWK_PATH=`` as unset, not as the current directory.

        Pydantic turns an empty string into ``Path('.')``, which is truthy and
        resolves to the repository root: a deployment that copied the blank
        line from ``.env.example`` would have "a key" that is a directory.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("operator_key_sha256", mode="before")
    @classmethod
    def _blank_digest_is_unset(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or None
        return value

    @property
    def operator_key_digest(self) -> str | None:
        """``OPERATOR_KEY_SHA256`` when it is a SHA-256 digest, else ``None``: the page is off.

        Pasting the key where its digest goes is the mistake this setting
        invites, and it would fail quietly: every key presented would be
        hashed and compared with a key, and never match. Not refused at
        startup, though, because a typo in an optional setting must not keep
        the books from opening on the next restart. The page stays off and
        says why (``require_operator``), and ``create_app`` logs a warning.
        """
        value = self.operator_key_sha256
        if value is None or len(value) != 64:
            return None
        return value if all(char in "0123456789abcdef" for char in value) else None

    @property
    def bankid_key_material(self) -> bytes | None:
        """The BankID client key given inline, or ``None``."""
        return _key_material(self.bankid_jwk)

    @property
    def bankid_key_file(self) -> Path | None:
        """The BankID client key file as an absolute path, or ``None``."""
        return _repo_relative(self.bankid_jwk_path)

    @property
    def session_cookie_secure(self) -> bool:
        """HTTPS-only cookies everywhere except local development.

        Derived rather than configurable: a deployment that has to *opt in* to
        secure cookies is a deployment where someone will forget.
        """
        return self.environment != "development"


def _key_material(secret: SecretStr | None) -> bytes | None:
    """Signing-key bytes from an inline setting, or ``None`` when it is blank.

    Newlines survive a secret binding badly, so a PEM pasted as a single line
    with ``\n`` escapes is accepted as well as a real multi-line one.
    """
    if secret is None:
        return None
    raw = secret.get_secret_value().strip()
    if not raw:
        return None
    if "\\n" in raw and "\n" not in raw:
        raw = raw.replace("\\n", "\n")
    return raw.encode("utf-8")


def _repo_relative(path: Path | None) -> Path | None:
    """Resolve a configured path against the repository root, not the cwd.

    ``.env`` documents ``./secrets/maskinporten.jwk.json``, and the backend is
    normally started from ``backend/`` — so a relative path read literally would
    point at a file that is not there. Anchoring on the repo root makes the
    documented value mean what it looks like it means, and an absolute path
    (what production should use) passes through untouched.
    """
    if path is None:
        return None
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
