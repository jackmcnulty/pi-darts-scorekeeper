"""The pure scoring engine.

Every module under this package is deterministic and dependency-free: no I/O, no
clock, no randomness, no framework types, and no imports from the persistence,
service or API layers. That is what makes the engine exhaustively testable, and
it is enforced mechanically by `tests/engine/test_purity.py` rather than by
convention — see that module for the exact ban list.

Import the names you need from the submodule that defines them
(`darts.engine.throws`), so the guard has a real import graph to walk.
"""
