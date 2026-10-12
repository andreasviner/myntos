# EHF validation artefacts

The published rule sets an EHF / Peppol BIS Billing 3.0 document is judged by,
vendored **unmodified** so `tests/test_faktura_ehf_validation.py` can run the
generator's output through them offline. Fetched 2026-10-09.

Unmodified is the point. A schema or Schematron edited to make our output pass
would turn the test into a statement about ourselves; the files here are only
worth anything as long as their bytes are the publisher's. The checksums below
are what was downloaded — `sha256sum -c` against them before trusting a change.
`.gitattributes` marks the directory `-text` and `.pre-commit-config.yaml`
excludes it from the whitespace fixers, so neither git nor a hook rewrites a
line ending or a trailing space on the way in.

| Directory | What | Version | Source | Licence |
| --- | --- | --- | --- | --- |
| `ubl-2.1/xsd/` | OASIS UBL 2.1 XSDs: `maindoc/` Invoice and CreditNote, and all of `common/` they import | UBL 2.1 OS (4 Nov 2013) | <https://docs.oasis-open.org/ubl/os-UBL-2.1/UBL-2.1.zip>, `xsd/` | OASIS IPR Policy; OASIS specifications may be copied and distributed unmodified (notice in each file) |
| `cen-en16931-1.3.16/` | CEN TC 434 EN 16931 rules bound to UBL, **pre-compiled XSLT** as CEN publishes it | validation-1.3.16 (2026-04-13) | <https://github.com/ConnectingEurope/eInvoicing-EN16931/releases/tag/validation-1.3.16>, `en16931-ubl-1.3.16.zip` → `xslt/EN16931-UBL-validation.xslt` | EUPL 1.2 (header of the file) |
| `peppol-bis-billing-3.0.21/PEPPOL-EN16931-UBL.sch` | OpenPeppol's rules on top of EN 16931, including the Norwegian NO-R-* rules | Peppol BIS Billing 3.0.21 (published 2026-05-20, mandatory from 2026-08-17; it is the release that adopts CEN 1.3.16) | <https://docs.peppol.eu/poacc/billing/3.0/files/PEPPOL-EN16931-UBL.sch> | © OpenPeppol AISBL, see below |
| `peppol-bis-billing-3.0.21/examples/` | The official example documents, used as the control | 3.0.21 (`BIS-Billing3-Examples.zip`), plus `Norwegian-example-1.xml` | <https://docs.peppol.eu/poacc/billing/3.0/files/BIS-Billing3-Examples.zip>; the Norwegian example from tag `v3.0.20` of <https://github.com/OpenPEPPOL/peppol-bis-invoice-3>, `rules/national-examples/NO/` (3.0.21 is not tagged there yet) | © OpenPeppol AISBL |
| `schxslt-1.10.1/` | SchXslt, the Schematron-to-XSLT compiler, `xslt/2.0/` only | 1.10.1 | Maven Central, `name.dmaus.schxslt:schxslt:1.10.1` jar; licence from <https://codeberg.org/SchXslt/schxslt> | MIT, `schxslt-1.10.1/LICENSE` |

## Why the Peppol rules are a `.sch` and the CEN rules an `.xslt`

CEN publishes its rules pre-compiled, and that file is used as is. OpenPeppol
publishes only the Schematron source: its own validator compiles it at run time,
and no compiled XSLT is offered on docs.peppol.eu or in the GitHub repository.
So the test compiles the `.sch` itself with SchXslt in Saxon-HE. Vendoring a
compiled copy instead would put a generated file in the tree that no upstream
checksum can vouch for.

Both rule sets are XSLT 2.0, which lxml cannot run; Saxon-HE (`saxonche`,
MPL-2.0) is a dev dependency for that reason only. lxml does the XSD.

## The CEN examples are not here

The CEN release also ships example invoices, but they carry the bare EN 16931
`CustomizationID` rather than Peppol's, and Peppol's rules reject them for that
alone. They would test nothing about a Peppol document.

## OpenPeppol's terms

No open licence is attached to the Peppol validation artefacts. The BIS
document's statement of copyright says OpenPeppol AISBL holds the copyright,
developed "subject to the conditions of the agreement between the European
Commission and CEN allowing derivate works to be developed and distributed with
no additional licensing requirements", and that the BIS *document* may not be
modified or redistributed without consent. The `.sch` and the examples are
published for implementers to validate against and are redistributed unmodified
by every Peppol validator; they are kept here on that basis, unmodified, for
test use only. If that reading is ever challenged, the fix is to fetch them at
test time instead.

## Checksums (sha256)

```
39f9d282867f1a49e7708d9e29a53da89643e1ee56f10cec1ebcf1277595fcbd  cen-en16931-1.3.16/EN16931-UBL-validation.xslt
aa3df18eb8c634624637eb229891d989c5cfb7cd0d08894ff8e58c58f247ea5b  peppol-bis-billing-3.0.21/examples/Allowance-example.xml
08e0ad82e0dbe7e16d7533c01761843343a56954ea24881d0f7f1cce06f8879e  peppol-bis-billing-3.0.21/examples/base-creditnote-correction.xml
97e9dd5fc1747a9a2447b38bae8d1de3317d2636714369bdd99ba7dd1fb0ee1a  peppol-bis-billing-3.0.21/examples/base-example_profile02.xml
1b7cc3ff1834c8963f2c93f30f171b58002cbf0b2c52dc8765e7e83aebb9f7c9  peppol-bis-billing-3.0.21/examples/base-example.xml
000781ee8cb7794a140bb1308f7f7a2c9ded3623571b4297aef38423971ab5a4  peppol-bis-billing-3.0.21/examples/base-negative-inv-correction.xml
a010c23fb221907eee7d80a7feb1575ce9989fd8b491a473e91069562a5780aa  peppol-bis-billing-3.0.21/examples/Norwegian-example-1.xml
cdb84e4ce1a770f6e4a8949dcbe37493bc37aeded5232b592a1a48feb221a504  peppol-bis-billing-3.0.21/examples/sales-order-example.xml
4f2f20de65a8c088bba6c43177c75b5066bf30f54b996bc1729a82097a6591db  peppol-bis-billing-3.0.21/examples/vat-category-E.xml
212c4eede960a775f8e79dca6cd6430e6f8c105560fbfdeaa03d5e6587eaa1ec  peppol-bis-billing-3.0.21/examples/vat-category-O.xml
59f96ae9a77ed3eda4ac17f499994fbd1b050432edf3bb8c117d7f3bca8d5f95  peppol-bis-billing-3.0.21/examples/Vat-category-S.xml
fed3f3f393591e1c628f2da4a3aa77e8c66e4ed8ed4368e18837782beab60daa  peppol-bis-billing-3.0.21/examples/vat-category-Z.xml
62e5b67892f12755352d78b06f63229a02cc2eccc748677c56efbc8dbcb336e3  peppol-bis-billing-3.0.21/PEPPOL-EN16931-UBL.sch
d4df046564af3ba3d9f10976e29cbea1a6c45c746e3e53efcea8377c42c3c8e9  schxslt-1.10.1/xslt/2.0/compile/api-2.0.xsl
5f24b8b28be50d650542c5113185ed55983beeaad061eddc4b966ca354751a57  schxslt-1.10.1/xslt/2.0/compile/compile-2.0.xsl
9f64f1ea1164094f1b6dd20d67b40af8b124f3a3579fa2dec07c64359cc108b3  schxslt-1.10.1/xslt/2.0/compile-for-svrl.xsl
e7d96717677ac768a72edda3b9f45e67c40476525247906271c42b652cf3b397  schxslt-1.10.1/xslt/2.0/compile/functions.xsl
afd07c44eeb70b8e9daf0feb1319ca655468f06543437c8ecf0e5a232b3038c8  schxslt-1.10.1/xslt/2.0/compile/templates.xsl
97bc8bee61b23592fe4937f261b47cb0e3725c25516084a9788d33e102cbba79  schxslt-1.10.1/xslt/2.0/expand.xsl
ab6f9359c7a1a17a589568d91bab766f53fc98064beb8cc976e6e3319afb2b24  schxslt-1.10.1/xslt/2.0/include.xsl
64a535e6d0f0baa809f987d27d9e5b825d420e9ebda7344ef53edf136cdd42fa  schxslt-1.10.1/xslt/2.0/pipeline-for-svrl.xsl
d0189741f5e6119b76b6914413e1a7c92427efef6f2ecb9b2c34e833c533ec1f  schxslt-1.10.1/xslt/2.0/pipeline.xsl
d2d8ea16cdbb6b0d0efd6d39955459ebcdf511000c24dda38220961f1d7e2611  schxslt-1.10.1/xslt/2.0/svrl.xsl
37a75ed72e61a820a29e583e483ac9f32bb9d0615513f27f944ac9b2b1e5ed68  schxslt-1.10.1/xslt/2.0/util/normalize-svrl.xsl
405b624fad86d226377a32884ad5cda771d697b7e3f15443f0601c12bfe0018e  schxslt-1.10.1/xslt/2.0/version.xsl
dd546e4809df86b6445589f69f0d6c9df162840ae386574ddfc1da7638103e15  ubl-2.1/xsd/common/CCTS_CCT_SchemaModule-2.1.xsd
939172ad8dd057cd403e7f763f6532184dd5ed4b9de24c42ebb35db4792ba613  ubl-2.1/xsd/common/UBL-CommonAggregateComponents-2.1.xsd
bd4ad043ee1d9da1c7f8018dabf739cfafdfb59143d0d16b9ef769e6b7c408a7  ubl-2.1/xsd/common/UBL-CommonBasicComponents-2.1.xsd
ad7a4e490978adfbcfc5ec0bb20941cf11ac960ccf0c4de8791a7c731a8dbe87  ubl-2.1/xsd/common/UBL-CommonExtensionComponents-2.1.xsd
3db472305f029bba5c1ae157bfd0178f715c3f9b94bd8e6c557dbce5e88da874  ubl-2.1/xsd/common/UBL-CommonSignatureComponents-2.1.xsd
8be3379dbdcbcc7802fafdd16bac72c48fff1c0bb364213a31a17911dd06100b  ubl-2.1/xsd/common/UBL-CoreComponentParameters-2.1.xsd
fcee77a11870208e6377ea6311b9f2a050bca24bdad8606ea02d71e9f9e72f8d  ubl-2.1/xsd/common/UBL-ExtensionContentDataType-2.1.xsd
7dcb156e610239c97ae70940cf4653b88e48c3595bf5f56a2204a32e2893e6cf  ubl-2.1/xsd/common/UBL-QualifiedDataTypes-2.1.xsd
17bb6b62d709b4fd81449a37655af36aa6a1276ad4fdb1b2e249a5ed4b7c2172  ubl-2.1/xsd/common/UBL-SignatureAggregateComponents-2.1.xsd
cef924d7ba3d1d8ade14469325cde1364f8c174e46f0198ec02da8e9e748a489  ubl-2.1/xsd/common/UBL-SignatureBasicComponents-2.1.xsd
09052d406b4293e2a5f9c2bfee6df10ad4d8d5f0b36e24a6349d7f7936d89eb6  ubl-2.1/xsd/common/UBL-UnqualifiedDataTypes-2.1.xsd
a4f726bcf8cc3f7d9ffa4dab99e005535a8e8b60dced1e5d94578d2e05afa96e  ubl-2.1/xsd/common/UBL-XAdESv132-2.1.xsd
1fa4625e9cefcb7a9abb5ac1b64315547450031eece8a55bd584e4ba4b79dbc1  ubl-2.1/xsd/common/UBL-XAdESv141-2.1.xsd
101909c9f06456d61ddcc4fb982f1d40dc357b439f393b1a2eb46e42acd60809  ubl-2.1/xsd/common/UBL-xmldsig-core-schema-2.1.xsd
a54651b1225052f811bf2ba01346f13f2454e7cc3e0be290c91dd680dc7b7b1a  ubl-2.1/xsd/maindoc/UBL-CreditNote-2.1.xsd
40fae8cb436f3a9506d7acce65ba162caef3b0bed4d5cbc0992b2153ded4edf4  ubl-2.1/xsd/maindoc/UBL-Invoice-2.1.xsd
```
