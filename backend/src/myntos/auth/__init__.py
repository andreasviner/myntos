"""Authentication towards Norwegian government services.

Two complementary mechanisms (both required — see docs/integrations/):

- ``maskinporten``: machine-to-machine tokens. Accepted by Skatteetaten for
  retrieval/information APIs (e.g. fetching the current skattemelding).
- ``idporten``: personal end-user login (OIDC authorization-code). Required
  by Skatteetaten for validating and submitting the MVA-melding and the
  skattemelding as of 2026.

Tokens from either are exchanged for Altinn tokens before calling Altinn
instance APIs.
"""
