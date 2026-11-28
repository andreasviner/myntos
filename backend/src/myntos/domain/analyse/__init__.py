"""Analyse: a company's income and costs from its books, and KI reading them.

docs/analyse.md is the contract: the tables, the JSON shapes, the rules and
the work packages. Read it before changing anything here, and change it in the
same commit when a shape changes.

Two halves that never mix their duties:

- The figures (:mod:`.figures`, :mod:`.datasets`, :mod:`.charts`,
  :mod:`.sentences`, :mod:`.overview`, :mod:`.errors`) are computed from the
  books on every request and never ask a model. Every number a person sees
  comes from here.
- KI reading the books (:mod:`.models`, :mod:`.vocabulary`, :mod:`.items`,
  :mod:`.tagging`, :mod:`.freshness`, :mod:`.jobs`) runs in the background,
  stores a stamped summary and tags per voucher, and is never shown as such.
  The figures may *group by* those tags; they never take a number from them.

The package imports from the ledger, never the other way round, and does not
import :mod:`myntos.integrations` beyond the ``Level`` enum that
``myntos.domain.ki.service`` speaks too: the ask function is injected, the way
:mod:`myntos.domain.bilag.classification` takes it.
"""
