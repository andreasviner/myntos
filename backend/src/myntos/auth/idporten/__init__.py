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

Implemented in the MVA milestone (docs/ROADMAP.md); flow details in
docs/integrations/mva.md and docs/integrations/maskinporten.md.
"""
