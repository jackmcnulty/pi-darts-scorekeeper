"""Two maintenance buttons: publish a snapshot, and rebuild the replay caches.

Unauthenticated, like every other route on this box. There is one household on
one LAN behind one router, and inventing an auth story for two endpoints while
`POST /api/matches` and `POST /api/matches/{id}/abandon` stay open would be
theatre rather than security. `docs/architecture.md` records the assumption so
that whoever eventually exposes this beyond the LAN knows what it rests on.

Both are synchronous and both are idempotent. A 50,000-dart database copies in
well under a second, so there is no case for a 202 and a job to poll: the
response *is* the result, and a second press produces the same answer as the
first. Neither is a `PUT` because neither addresses a resource the caller names
-- they are actions, and `POST` is the verb for an action.
"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from darts.api.deps import ConnectionDep, SettingsDep
from darts.services import admin as service
from darts.services import snapshot as snapshot_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


class SnapshotResponse(BaseModel):
    """The manifest, plus where it and its database went.

    The same facts `snapshot.json` carries, so a client that called this and a
    client that read the file off #30's share are looking at one answer.
    """

    snapshot: str
    path: str
    manifest_path: str
    created_at: str
    schema_version: int
    size_bytes: int
    row_counts: dict[str, int]


class RebuildResponse(BaseModel):
    """What the sweep found. `legs_changed` is 0 on a healthy database."""

    model_config = ConfigDict(from_attributes=True)

    legs_visited: int
    legs_changed: int
    rows_before: int
    rows_after: int


@router.post("/snapshot", response_model=SnapshotResponse)
def take_snapshot(settings: SettingsDep) -> SnapshotResponse:
    """Republish `darts-latest.db` and `snapshot.json` in the snapshot directory.

    Safe mid-game: the copy is a point-in-time image taken inside a read
    transaction, and it is renamed into place, so a reader on the share either
    keeps the file it already opened or gets the new one whole.
    """
    result = snapshot_service.create(settings.db_path, settings.snapshot_dir)
    return SnapshotResponse(
        snapshot=snapshot_service.SNAPSHOT_NAME,
        path=str(result.path),
        manifest_path=str(result.manifest_path),
        created_at=result.created_at,
        schema_version=result.schema_version,
        size_bytes=result.size_bytes,
        row_counts=result.row_counts,
    )


@router.post("/rebuild-caches", response_model=RebuildResponse)
def rebuild_caches(conn: ConnectionDep) -> RebuildResponse:
    """Recompute every leg's replay cache from its darts.

    The caches are disposable -- they exist only to resume an interrupted leg --
    so this can never lose history, and on a database that is already correct it
    changes nothing and reports `legs_changed: 0`.
    """
    return RebuildResponse.model_validate(service.rebuild_caches(conn))
