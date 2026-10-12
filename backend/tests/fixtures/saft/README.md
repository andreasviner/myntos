# Skatteetaten's SAF-T Financial files, copied verbatim

From <https://github.com/Skatteetaten/saf-t>, commit `05179521e435`, fetched
2026-10-09. That repository carries no licence file; the schemas and examples are
the published standard every Norwegian accounting system is told to validate
against. The General Ledger Standard Accounts lists in the same repository are
Regnskap Norge's copyright and are deliberately **not** copied here.

- `Norwegian_SAF-T_Financial_Schema_v_1.30.xsd` (`SAF-T_Financial_1.3/`): valid for
  periods from 2025-01-01, usable until 2026-12-31.
- `Norwegian_SAF-T_Financial_Schema_v_1.40.xsd` (`SAF-T_Financial_1.4/`): the only
  valid format for periods from 2027-01-01.
- `example_v_1.30.xml`, `example_v_1.40.xml`: `generated_auditfile_formatted_v_1.x0.xml`
  from each version's `Example Files/`. Both validate against their schema, which
  `tests/test_saft_export.py` checks first, so a failure there is about the schema
  or the validator and not about our exporter.

Replace them, do not edit them. If Skatteetaten publishes a new version, the
test failing against it is the point.
