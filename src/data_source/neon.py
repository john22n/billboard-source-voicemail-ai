import os
from typing import Any

import aiohttp
import psycopg
from psycopg.rows import dict_row


NEON_CONNECTION_URI_URL = (
    "https://console.neon.tech/api/v2/projects/"
    "spring-night-59724844/connection_uri"
)


async def _get_database_url() -> str:
    if database_url := os.getenv("DATABASE_URL"):
        return database_url

    api_key = os.getenv("NEON_API_KEY")
    if not api_key:
        raise RuntimeError("DATABASE_URL or NEON_API_KEY is required")

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            NEON_CONNECTION_URI_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            params={
                "database_name": "neondb",
                "role_name": "neondb_owner",
                "pooled": "true",
            },
        ) as response:
            response.raise_for_status()
            data = await response.json()
            return data["uri"]


async def get_billboard_locations() -> list[dict[str, Any]]:
    """Return every billboard location stored in Neon."""
    database_url = await _get_database_url()

    async with await psycopg.AsyncConnection.connect(
        database_url,
        row_factory=dict_row,
    ) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT * FROM billboard_locations")
            return await cursor.fetchall()
