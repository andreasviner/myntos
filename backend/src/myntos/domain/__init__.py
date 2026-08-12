"""Accounting domain core: ledger (hovedbok), bilag and mva.

Pure business logic. This package must not import from ``api``,
``integrations`` or ``storage`` — those depend on the domain, never the
reverse. Norwegian domain terms are used deliberately: they are precise legal
concepts from bokføringsloven and skatteforvaltningsloven.
"""
