# Brønnøysundregistrene's RR-0002 (Årsregnskap vanlig) schemas, copied verbatim

From <https://github.com/brreg/docs> (`master`),
`content/apidokumentasjon/regnskapsregisteret/dokumentasjon-datamodeller-og-designmaler/RR-0002-Vanlig/Datamodell/`,
fetched 2026-11-28 and verified against the live JSON schemas served by
`https://brg.apps.altinn.no/brg/aarsregnskap-vanlig-202406/api/jsonschema/{Hovedskjema,Underskjema}`
and TT02 (`brg.apps.tt02.altinn.no`).

- `RR-0002_1266-51820.xsd` (`dataFormatId="1266"`, `dataFormatVersion="51820"`, live Hovedskjema)
  SHA-256: `4b0e41082dda49ad44dd1cc1a0d399a8605330ce9553710411a3fc7e9aacd929`
- `RR-0002-U_758-51980.xsd` (`dataFormatId="758"`, `dataFormatVersion="51980"`, live Underskjema)
  SHA-256: `51f8c3df856586533ad6f4a6ded8abf715578b7fc603ee55d55a97f1d4ad1d37`
- `RR-0002_1266-51820_januar_2027.xsd` (stricter Hovedskjema taking effect January 2027)
  SHA-256: `deef6820e5df44fef68ab533a907c0ada338869aed0e8882cc2cb8db67050459`
- `RR-0002-U_758-51980_januar_2027.xsd` (stricter Underskjema taking effect January 2027)
  SHA-256: `d839dc4cf17110ead1140ccbeee3acfacd224686a51aa4e6e55b440f61e0f1a0`

Replace them, do not edit them. Generated XML is validated against both the
live schemas and the January 2027 schemas in `tests/test_aarsregnskap_xml.py`.
