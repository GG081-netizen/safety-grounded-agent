"""PostgreSQL / SQLAlchemy async persistence package (M1.4).

Sub-packages and modules:
  engine.py          — DatabaseEngine (async lifecycle, session factory)
  unit_of_work.py    — UnitOfWork Protocol
  repository.py      — DatabaseRepository abstract base
  null_persistence.py — NullUnitOfWork + NullRepository (no-op persistence)

M1.4-C adds a narrow execution repository and short-transaction unit of work.
FastAPI wiring, runtime idempotency, replay, and fencing remain out of scope.
"""
