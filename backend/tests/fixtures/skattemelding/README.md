# Skattemelding fixtures

Synthetic data only (Tenor), as `test_data/README.md` requires of anything sent
to TT02.

- `tt02-utkast-314067630-2025.xml`: the fetch's answer for the same AS's 2025
  utkast in TT02, a copy of `test_data/skattemelding-314067630-2025.xml`: an
  upersonlig skattemelding with only `partsnummer` (3001570199) and
  `inntektsaar`, as every Tenor company's is.
- `tt02-validering-314067630-2025.xml`: Skatteetaten's answer from
  `/api/skattemelding/v2/valider/2025/314067630` in TT02 on 2026-10-21, to the
  envelope `digdir_check --person 08866995401 --skattemelding 314067630
  --year 2025` built: the synthetic AS's empty utkast
  (`test_data/skattemelding-314067630-2025.xml`) with Myntos's formue and
  opplysninger added, and a næringsspesifikasjon from `sample_grunnlag`. The
  verdict is `validertOK`, the computed tax 83 820, and every `avvik` is a
  derived value Skatteetaten filled in itself (`mangler…`), which is the
  pattern a correct melding that sends no derived values gives.
- `tt02-tilbakemelding-314067630-2025.xml`: Skatteetaten's answer
  (`tilbakemelding`) on the Altinn instance after the same envelope was filed
  in TT02 on 2026-10-22 (`digdir_check … --submit`, instance
  `51889108/21800e12-17db-4e38-931d-d154aa24ce3d`), when Altinn's status read
  «Mottatt: Din skattemelding er mottatt». The same document type as the
  validation's answer, computed again on what was filed.

The XSDs are not here: they are read at runtime and live in the package
(`src/myntos/skattemelding/xsd/`).
