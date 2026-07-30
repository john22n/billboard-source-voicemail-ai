import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.data_source.neon import (
    MAX_LOCATION_DISTANCE,
    _location_embedding_input,
    get_location_pricing,
)


class NeonTests(unittest.TestCase):
    def test_location_embedding_uses_billboard_search_context(self) -> None:
        self.assertEqual(
            _location_embedding_input("Dallas, Texas"),
            "billboard advertising in Dallas, Texas",
        )

    def test_location_pricing_uses_vector_similarity(self) -> None:
        expected = {"city": "Dallas", "state": "TX"}
        cursor = AsyncMock()
        cursor.__aenter__.return_value = cursor
        cursor.fetchone.return_value = expected
        connection = MagicMock()
        connection.__aenter__ = AsyncMock(return_value=connection)
        connection.__aexit__ = AsyncMock(return_value=None)
        connection.cursor.return_value = cursor

        with (
            patch(
                "src.data_source.neon._get_location_embedding",
                new=AsyncMock(return_value=[0.1, 0.2]),
            ),
            patch(
                "src.data_source.neon._get_database_url",
                new=AsyncMock(return_value="postgresql://example"),
            ),
            patch(
                "src.data_source.neon.psycopg.AsyncConnection.connect",
                new=AsyncMock(return_value=connection),
            ),
        ):
            result = asyncio.run(get_location_pricing("Dallas, Texas"))

        self.assertEqual(result, expected)
        sql, params = cursor.execute.await_args.args
        self.assertIn("embedding <=> %s::vector", sql)
        self.assertIn("distance <= %s", sql)
        self.assertEqual(params, ("[0.1, 0.2]", MAX_LOCATION_DISTANCE))

    def test_location_pricing_returns_none_when_no_match_is_close_enough(self) -> None:
        cursor = AsyncMock()
        cursor.__aenter__.return_value = cursor
        cursor.fetchone.return_value = None
        connection = MagicMock()
        connection.__aenter__ = AsyncMock(return_value=connection)
        connection.__aexit__ = AsyncMock(return_value=None)
        connection.cursor.return_value = cursor

        with (
            patch(
                "src.data_source.neon._get_location_embedding",
                new=AsyncMock(return_value=[0.1, 0.2]),
            ),
            patch(
                "src.data_source.neon._get_database_url",
                new=AsyncMock(return_value="postgresql://example"),
            ),
            patch(
                "src.data_source.neon.psycopg.AsyncConnection.connect",
                new=AsyncMock(return_value=connection),
            ),
        ):
            result = asyncio.run(get_location_pricing("London, England"))

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
