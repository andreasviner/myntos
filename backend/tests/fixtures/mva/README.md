# Skatteetaten's MVA-melding files, copied verbatim

From <https://github.com/Skatteetaten/mva-meldingen> (`docs/informasjonsmodell_filer/`),
fetched 2026-10-06, under that repository's Apache License 2.0.

- `no.skatteetaten.fastsetting.avgift.mva.skattemeldingformerverdiavgift.v1.0.xsd`:
  the schema every melding Myntos writes is validated against in
  `tests/test_mva_melding.py`.
- `betalingsinformasjon.xml`, `valideringsresultat.xml`: the feedback from a real
  test submission (`example_files/feedback/exampleSuccessfulFeedback17062021/`),
  which the parsers are tested on.

Added 2026-10-09, from the same repository at commit
`05c8b14eafc33907a759208c9a66d1d9d31d34d7`, same licence:

- `eksempelMedAlleTilfeller.xml` (`example_files/melding/`): Skatteetaten's own
  melding carrying every code and all four ordinary `spesifikasjon` values.
  Validated against the XSD as a control, and the shape of each of its
  spesifikasjon lines (which elements are present, which sign) is what
  `tests/test_mva_spesifikasjon.py` holds Myntos's output to.
- `kodelister/mvaKodeSAFT.xml`, `kodelister/mvaSpesifikasjon.xml`
  (`kodelister/`): which spesifikasjon each code may carry (`uttak`,
  `tapPaaKrav`, `justering`, `tilbakefoeringAvInngaaendeMerverdiavgift` flags
  per code) and how each spesifikasjon is spelled. The table in
  `myntos.domain.mva.codes.SPESIFIKASJON_CODES` is tested against them.

Replace them, do not edit them. If Skatteetaten publishes a new version, the
test failing against it is the point.
