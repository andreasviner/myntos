"""The skattemelding and næringsspesifikasjon: built from the books, checked, and sent.

A sibling of :mod:`myntos.saft` rather than a part of the domain, for the same
reason: it is a projection of the ledger into a document Skatteetaten defines,
read against Skatteetaten's own published files (``README.md``), and it has to
change every income year when they do.

- :mod:`.schema` reads the year's XSD for element order, cardinality and what
  Skatteetaten derives itself; :mod:`.xmlwriter` writes a tree in that order
  and refuses what may not be sent.
- :mod:`.codes` reads the year's code lists.
- :mod:`.naeringsspesifikasjon` builds the næringsspesifikasjon from plain
  figures; :mod:`.upersonlig` adds what an aksjeselskap's own skattemelding
  needs to the utkast Skatteetaten serves.
- :mod:`.envelope` wraps both for validation and Altinn, and reads the answers.
- :mod:`.service` and :mod:`.models` are the one part that reads the
  database: the figures from the books (through :mod:`myntos.saft.service`,
  the anleggsregister and the Enhetsregisteret snapshot), and the filings,
  each kept with the exact envelope that was checked.

Everything else here is pure: plain figures in, XML out, tested against
Skatteetaten's XSDs without a database or a network. The client that talks to
Skatteetaten and Altinn is :mod:`myntos.integrations.skatteetaten.client`, and
the routes are :mod:`myntos.api.v1.skattemelding`.
"""
