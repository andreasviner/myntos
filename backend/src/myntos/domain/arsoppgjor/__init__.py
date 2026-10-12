"""Where a set of books starts and where each of its years ends.

Two halves of one question, which is why they share a package: what the balance
sheet said on the first morning, and what happened to the result on the last
evening.

- :mod:`.opening`: the **inngående balanse** of a company whose earlier years
  were kept somewhere else, posted as one balanced voucher in its own journal.
  :mod:`.saldobalanse` reads the previous system's saldobalanse as CSV into the
  same lines a person could type.
- :mod:`.closing`: **årsavslutning**. The year's result, the tax an AS owes on
  it, the disposition onto equity (which differs between an AS and an ENK), the
  lock, and the next year opened. Reopening is an explicit, logged act that
  reverses the closing rather than deleting it.

Nothing here edits a posted voucher. A wrong opening balance is reversed and
replaced; a reopened year keeps both its closing and the reversal of it. The
log of who closed and reopened what, and why, is :class:`.models.FinancialYearEvent`.

The package imports from the ledger and the MVA domain, never the other way
round, so nothing in the core depends on whether this exists.
"""
