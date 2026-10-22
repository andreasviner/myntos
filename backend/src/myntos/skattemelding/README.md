# Skatteetaten's skattemelding schemas and code lists, copied as published

From <https://github.com/Skatteetaten/skattemeldingen>, commit `2bdf03ba3b3c`
(2026-10-21), fetched 2026-10-21. Apache-2.0: the licence is
`LICENSE-skattemeldingen` beside this file; the repository has no NOTICE file.
Read at runtime by `myntos.skattemelding`, which is why they live in the package
and not under `tests/`.

## `xsd/`: the XSDs, byte for byte

| File | Repository path | What it is for |
| --- | --- | --- |
| `naeringsspesifikasjon_v6_ekstern.xsd` | `src/resources/xsd/` | The næringsspesifikasjon for inntektsår **2025** |
| `naeringsspesifikasjon_v7_ekstern.xsd` | `src/resources/xsd/` | The same for **2026**. Not final: Skatteetaten edits it until about 15 January |
| `skattemeldingUpersonlig_v5_ekstern.xsd` | `src/resources/xsd/` | An aksjeselskap's own skattemelding for **2025**, which Myntos fetches as an utkast and sends back with `formueOgGjeld` and `opplysningOmSkattesubjekt` added |
| `skattemeldingUpersonlig_v6_ekstern.xsd` | `src/resources/xsd/` | The same for **2026** |
| `skattemeldingognaeringsspesifikasjonrequest_v2.xsd` | `src/resources/xsd/` | The envelope that carries the skattemelding and the næringsspesifikasjon to validation and to Altinn, every year |
| `skattemeldingognaeringsspesifikasjonresponse_v2.xsd` | `src/resources/xsd/` | What validation answers |

The first 16 hex digits of each file's SHA-256, to tell a later copy from this
one:

| File | SHA-256 |
| --- | --- |
| `naeringsspesifikasjon_v6_ekstern.xsd` | `6300d00b31f4cb1c` |
| `naeringsspesifikasjon_v7_ekstern.xsd` | `e38aa38644131a65` |
| `skattemeldingUpersonlig_v5_ekstern.xsd` | `d8e74eda092540a9` |
| `skattemeldingUpersonlig_v6_ekstern.xsd` | `c05bcbfbaf8101f6` |
| `skattemeldingognaeringsspesifikasjonrequest_v2.xsd` | `ac6f939fe895a9a0` |
| `skattemeldingognaeringsspesifikasjonresponse_v2.xsd` | `60109b3338bb81d0` |

The folder is not called `schema/`, beside `schema.py`, because a directory
and a module of one name in one package are two different things to Python and
to mypy: Python imports the module, mypy reads the directory as a namespace
package and finds nothing in it.

The XSDs are not only checked against: `schema.py` reads them to decide the
order of every element written and to refuse the ones Skatteetaten derives
itself (`skatt:erAvledet="true"`). The order changes between versions (v7
moved `sumEgenkapital` after the lists), and a template written by hand for one
year is wrong the next.

## `codes/`: the code lists, byte for byte, gzipped

`src/resources/kodeliste/<year>/<year>_<name>.xml`, compressed with
`gzip -9 -n` because the result and balance list is 770 kB a year. `zcat` gives
back the published file exactly; the first 16 hex digits of each original's
SHA-256:

| File | SHA-256 of the published XML |
| --- | --- |
| `2025_resultatregnskapOgBalanse.xml.gz` | `a03b261b30405f9b` |
| `2025_permanentForskjellstype.xml.gz` | `dcd4f29e1498f74c` |
| `2025_egenkapitalendringstype.xml.gz` | `c76add26b77cb760` |
| `2025_midlertidigForskjellstype.xml.gz` | `f41475e6116a5b60` |
| `2025_regeltypeForAarsregnskap.xml.gz` | `d528aae34c757c98` |
| `2026_resultatregnskapOgBalanse.xml.gz` | `4d3de4f82e36b0c7` |
| `2026_permanentForskjellstype.xml.gz` | `e4b944562c44698e` |
| `2026_egenkapitalendringstype.xml.gz` | `4e595e54b675e645` |
| `2026_midlertidigForskjellstype.xml.gz` | `ea10c7ce2e62ac64` |
| `2026_regeltypeForAarsregnskap.xml.gz` | `3e400ef3cb97e3e1` |

The result and balance list is what says, per post, whether it is added or
subtracted (`kategori`), and which kinds of business and which regnskapsplikt
may use it. Skatteetaten refuses a post outside its regnskapsplikt
(`N_FEIL_*TYPE`, `faktiskFeil`), so Myntos checks it before sending, from the
same file.

## A new income year

Every year has its own XSD major and its own lists, published as a 0.9 model
about 30 November and frozen about 15 January, and edited after that too (v5 of
the upersonlig skattemelding changed in March 2026, the result and balance list
in August). For the next year:

1. Copy `src/resources/kodeliste/<year>/` files above, gzipped as above, and
   add their hashes here.
2. Copy the year's næringsspesifikasjon and upersonlig skattemelding XSDs into
   `xsd/`, add their hashes here, and add the year to
   `schema.NAERINGSSPESIFIKASJON` and `schema.SKATTEMELDING_UPERSONLIG` in
   `myntos.skattemelding.schema`, and to `codes.YEARS`.
3. Check the year's line in `docs/integrations/skatteetaten.md` (which XSD is
   which year), and run the tests: they validate a built document against the
   year's XSD.

The SAF-T grouping list (`myntos/saft/codes/`) and the result and balance list
are the same posts under the same category names, except that SAF-T has the
disposition (`resultatDisponeringForSAF-T`, 8800 and so on), which is not a
post in the næringsspesifikasjon, and the næringsspesifikasjon has posts SAF-T
does not (filialregnskap, `skjoennsfastsattInntektEllerKostnad`). `codes.py`
says which side wins where.
