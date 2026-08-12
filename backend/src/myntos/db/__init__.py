"""Database layer: SQLAlchemy base and session handling.

Model modules live next to their domain packages and register on
:class:`myntos.db.base.Base`. Schema changes go through Alembic
(``backend/migrations/``) — never ``create_all`` outside tests.
"""
