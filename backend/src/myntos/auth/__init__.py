"""Authentication — two entirely separate planes.

**Signing into Myntos** (this application's own accounts):

- ``passwords`` / ``tokens`` / ``sessions``: email-and-password credentials,
  opaque bearer tokens and revocable server-side login sessions.
- ``google``: "log in with Gmail" — Google's OIDC authorization-code flow.

The accounts themselves, and the organizations they belong to, live in
:mod:`myntos.domain.identity`.

**Authenticating towards Norwegian government services** — a different question
with different answers, and a Myntos session is never evidence for it. Two
complementary mechanisms (both required — see docs/integrations/):

- ``maskinporten``: machine-to-machine tokens. Accepted by Skatteetaten for
  retrieval/information APIs (e.g. fetching the current skattemelding).
- ``idporten``: personal end-user login (OIDC authorization-code). Required
  by Skatteetaten for validating and submitting the MVA-melding and the
  skattemelding as of 2026.

Tokens from either are exchanged for Altinn tokens before calling Altinn
instance APIs.
"""
