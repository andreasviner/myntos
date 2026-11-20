"""Frister: the deadlines a company has with Skatteetaten and Brønnøysund, and which are met.

docs/FEATURES.md row 22 is the feature; this is its first half. Every date here
is computed by Myntos from the law and from what the company's own books and
filings say, so it works for every company today, without the systembruker
that Skatteetaten's Frister API needs (docs/integrations/skatteetaten.md).
When that API is reachable, its date wins for any obligation both name: it
knows about postponements and changed terms that Myntos cannot see.

- :mod:`.rules`: the yearly deadlines per organisation form, with the
  paragraph each comes from, and nothing that touches a database.
- :mod:`.service`: the list for one company: which deadlines apply, which are
  met, and which Myntos cannot know about.

**A date is never later than the law's.** The MVA terms move off a weekend,
as :mod:`myntos.domain.mva.periods` already does; holidays are not modelled,
and the yearly dates are shown as the statute names them. Either way an error
can only make somebody early.

**«Done» is only said when Myntos knows.** An MVA term is done when it was
filed from Myntos or the person said another program filed it; an
aksjeselskap's skattemelding when it went from Myntos; an årsregnskap when
Brønnøysund's register lists it. Anything else is unknown, not missing: a
company that files the aksjonærregisteroppgave from another program must not
be told it is late.
"""
