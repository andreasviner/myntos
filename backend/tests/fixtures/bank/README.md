# camt.053 fixtures

Hand-built. No real bank data and no real names: the account is 1506.20.33158,
the number every test company is created with, and everything else is invented.

- `camt053_v02_mars.xml`: camt.053.001.02, the shape most Norwegian banks
  export. March 2026, IBAN, opening as OPBD, three booked entries with bank
  references, a KID on the incoming payment, and a `TxsSummry`.
- `camt053_v08_april.xml`: camt.053.001.08, the 2019 shape (`Pty` wrapper,
  `Sts/Cd`, BBAN in `Othr/Id`, opening as PRCD). Overlaps the March file from
  2026-03-16, repeating two entries under the same references; adds two
  identical card fees with no reference (the fingerprint case), one deposit, and
  one pending entry that must be left out.

The variants the tests need (unbalanced, foreign currency, a DTD) are derived
from these inside the tests, so each differs from a valid file in exactly the
way it is about.
