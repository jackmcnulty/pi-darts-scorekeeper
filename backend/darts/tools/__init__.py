"""Developer command-line tools.

These are not part of the running application and, unlike `darts.engine`, they
are free to touch the filesystem. That separation is the point: the engine's
purity guard stays honest because the generator under `darts.engine` only ever
returns text, and the writing happens out here.
"""
