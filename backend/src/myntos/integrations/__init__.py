"""Clients for external government APIs (Skatteetaten, Altinn, Enhetsregisteret).

Integrations authenticate via ``myntos.auth`` and must not contain
business logic — they translate between our domain and the external APIs.
Enhetsregisteret is the exception to the first half: it is open data and needs
no credentials at all.
"""
