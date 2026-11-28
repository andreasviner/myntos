# Skatteetaten's RF-1086 (Aksjonærregisteroppgaven) schemas, copied verbatim

From <https://github.com/Skatteetaten/api-dokumentasjon> (`main`),
`static/download/`, fetched 2026-11-27. That repository carries no licence
file; the XSDs are the published standard every Norwegian end-user system is
told to validate RF-1086 Hovedskjema and Underskjema XML against.

- `aksjonaerregisteroppgaveHovedskjema.xsd` (`skjemanummer="890"`, `RF-1086`)
  SHA-256: `194670070a54772ad7e026e37be1d87e7840dab7621c966a881e909d00d119df`
- `aksjonaerregisteroppgaveUnderskjema.xsd` (`skjemanummer="923"`, `RF-1086-U`)
  SHA-256: `a0f6b2a8d249f3f40290036314d3b1f5c1b3aed3ba90f58e5ce7b264c9ec33b7`

Replace them, do not edit them. If Skatteetaten publishes a new schema version,
`tests/test_aksjonaer_xml.py` failing against it is the point.
