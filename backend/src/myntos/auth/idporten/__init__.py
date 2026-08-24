"""ID-porten end-user authentication (OIDC authorization-code flow).

As of 2026, Skatteetaten requires a personal ID-porten login for VALIDATING
and SUBMITTING both the MVA-melding and the skattemelding — Maskinporten
(machine-to-machine) is only accepted for retrieval and information APIs.
The submission path therefore looks like:

1. The frontend redirects the user to ID-porten (authorization-code flow,
   scopes like ``openid skatteetaten:mvameldingvalidering
   skatteetaten:mvameldinginnsending``).
2. The backend receives the ID-porten access token.
3. The token is exchanged for an Altinn token at
   ``platform.altinn.no/authentication/api/v1/exchange/id-porten``.
4. The Altinn token drives the instance API calls (validate → submit →
   feedback).

Steps 1-3 are implemented — the flow itself in :mod:`.client`, the exchange in
:mod:`myntos.integrations.altinn.tokens`. What is deliberately *not* here is
where a government token lives between the callback and the submission: that
belongs to the MVA milestone (docs/ROADMAP.md phase 4) together with the
review-and-confirm step, because it is a question about a submission's audit
trail rather than about authentication.

Flow details in docs/integrations/mva.md and docs/integrations/maskinporten.md.
"""

from myntos.auth.idporten.client import (
    GovernmentLogin,
    IdPortenClient,
    IdPortenError,
    IdPortenNotConfiguredError,
    PkcePair,
    build_client,
    configured_scope,
    get_client,
    is_configured,
    new_pkce_pair,
    reset_client,
)

__all__ = [
    "GovernmentLogin",
    "IdPortenClient",
    "IdPortenError",
    "IdPortenNotConfiguredError",
    "PkcePair",
    "build_client",
    "configured_scope",
    "get_client",
    "is_configured",
    "new_pkce_pair",
    "reset_client",
]
