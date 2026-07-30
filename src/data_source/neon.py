import json
import os
from typing import Any, cast

import aiohttp
import psycopg
from psycopg.rows import dict_row


NEON_API_URL = "https://console.neon.tech/api/v2"
OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
EMBEDDING_DIMENSIONS = 512
MAX_LOCATION_DISTANCE = 0.20


def _location_embedding_input(location: str) -> str:
    return f"billboard advertising in {location}"


async def _get_location_embedding(location: str) -> list[float]:
    api_key = os.getenv("OPENAI_EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_EMBEDDING_API_KEY or OPENAI_API_KEY is required for location search"
        )

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            OPENAI_EMBEDDINGS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": os.getenv(
                    "OPENAI_EMBEDDING_MODEL",
                    "text-embedding-3-small",
                ),
                "input": _location_embedding_input(location),
                "dimensions": EMBEDDING_DIMENSIONS,
            },
        ) as response:
            response.raise_for_status()
            data = await response.json()
            return cast(list[float], data["data"][0]["embedding"])


async def _get_database_url() -> str:
    if database_url := os.getenv("DATABASE_URL"):
        return database_url

    api_key = os.getenv("NEON_API_KEY")
    project_id = os.getenv("NEON_PROJECT_ID")
    if not api_key or not project_id:
        raise RuntimeError(
            "DATABASE_URL or both NEON_API_KEY and NEON_PROJECT_ID are required"
        )

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            f"{NEON_API_URL}/projects/{project_id}/connection_uri",
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
        row_factory=cast(Any, dict_row),
    ) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT * FROM billboard_locations")
            return cast(list[dict[str, Any]], await cursor.fetchall())


async def get_location_pricing(location: str) -> dict[str, Any] | None:
    """Return a billboard pricing match within the maximum vector distance."""
    embedding = await _get_location_embedding(location)
    database_url = await _get_database_url()
    embedding_literal = json.dumps(embedding)

    async with await psycopg.AsyncConnection.connect(
        database_url,
        row_factory=cast(Any, dict_row),
    ) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT *
                FROM (
                    SELECT billboard_locations.*,
                           embedding <=> %s::vector AS distance
                    FROM billboard_locations
                    WHERE embedding IS NOT NULL
                ) AS nearest_location
                WHERE distance <= %s
                ORDER BY distance
                LIMIT 1
                """,
                (embedding_literal, MAX_LOCATION_DISTANCE),
            )
            return cast(dict[str, Any] | None, await cursor.fetchone())
