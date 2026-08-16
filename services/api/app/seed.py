from __future__ import annotations

import asyncio

from app.config import load_settings
from app.repositories import create_store
from app.security import SecretBox


async def seed() -> None:
    settings = load_settings()
    store = create_store(settings, SecretBox(settings.credential_encryption_key))
    await store.initialize()
    try:
        await store.seed_demo()
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(seed())
