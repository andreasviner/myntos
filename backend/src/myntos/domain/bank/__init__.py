"""Bank statements: camt.053 import and reconciliation against the register.

:mod:`.camt` reads the file, :mod:`.models` keeps it, :mod:`.matching` ranks
the vouchers a line might be the booking of, and :mod:`.service` ties them to
the database. See docs/integrations/bank.md for the format decisions.
"""
