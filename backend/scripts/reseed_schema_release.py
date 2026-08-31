"""Re-publish the schema file over whatever release is active.

Startup seeding deliberately refuses to overwrite an active release: reverting
an operator's activation on every restart would be a worse failure than the
invisibility it fixes. The cost of that rule is that once anything is active,
editing the file stops taking effect -- correctly, and silently.

This is the deliberate way back. It exists because the alternative, on a
developer's machine, is editing Mongo by hand.

    python backend/scripts/reseed_schema_release.py --check
    python backend/scripts/reseed_schema_release.py --apply

`--check` reports what is active, what the file says, and whether they agree.
`--apply` publishes the file's current content and points the runtime at it.
The previously active release is left published and is re-activatable by id --
this changes which release serves, it never destroys one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.release_seed import seeded_release_id
from return_platform.dynamic_knowledge.release_store import (
    ReleaseAlreadyPublished,
    SchemaReleaseStore,
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="publish and activate the file")
    parser.add_argument("--check", action="store_true", help="report without writing")
    arguments = parser.parse_args()
    if not (arguments.apply or arguments.check):
        parser.error("pass --check or --apply")

    settings = Settings()  # type: ignore[call-arg]
    schema = load_active_schema(settings.dynamic_knowledge_schema_path)
    release_id = seeded_release_id(schema.configuration_release_id, schema.configuration_checksum)

    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_dsn.get_secret_value()
    )
    try:
        releases = SchemaReleaseStore(client, settings.mongo_database)
        await releases.ensure_indexes()
        active = await releases.active()

        print(f"[reseed] file    {release_id}")
        print(f"[reseed] active  {active.configuration_release_id if active else '(none)'}")
        if active is not None and active.configuration_checksum == schema.configuration_checksum:
            print("[reseed] the active release is already this file's content")
            return 0
        if not arguments.apply:
            print("[reseed] they differ. Re-run with --apply to publish the file.")
            return 0

        try:
            await releases.publish(
                schema.model_copy(update={"configuration_release_id": release_id}),
                published_by="reseed",
            )
        except ReleaseAlreadyPublished:
            # This exact content was published before and then superseded.
            # Activating it is still what was asked for.
            print("[reseed] already published; activating it")
        plan = await releases.activate(release_id)
        print(f"[reseed] active  {release_id}")
        # The plan says what activation owes the graph. An operator who does not
        # see it will not know a rebuild is now required.
        print(f"[reseed] migration strategy: {getattr(plan, 'strategy', '(unknown)')}")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
