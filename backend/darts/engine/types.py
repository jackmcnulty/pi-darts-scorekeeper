"""Frozen dataclasses shared across more than one engine module.

Intentionally empty for now. #5 is vocabulary only, and the one type it defines
— `Throw` — has a module of its own (`darts.engine.throws`). Nothing is shared
yet, because there is nothing else in the engine to share it with. Inventing
types here ahead of the tickets that need them would be guessing at game
concepts this ticket explicitly puts out of scope.

The module exists so the home for such types is settled, and so the purity
guard already covers it on the day something lands here. Anything added must be
frozen, hashable and rule-free.
"""
