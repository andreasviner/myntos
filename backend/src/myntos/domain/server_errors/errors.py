"""Errors raised about the server's own error log.

Their own hierarchy, mapped to status codes in :mod:`myntos.api.errors` like
the others. Only the operator meets them (docs/operator.md).
"""


class ServerErrorLogError(Exception):
    """Base class for every refusal about the error log."""


class ServerErrorNotFoundError(ServerErrorLogError):
    """No recorded failure has this number: never recorded, or deleted after its time."""
