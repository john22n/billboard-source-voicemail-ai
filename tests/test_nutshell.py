import asyncio
import os
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import aiohttp

from src.data_source.nutshell import (
    NUTSHELL_API_URL,
    _find_or_create_contact,
    _nutshell_request,
    _resolve_assignee_id,
    create_nutshell_lead,
)
from src.models.sales_call_lead import LeadInformation


class NutshellTests(unittest.TestCase):
    def test_credentials_are_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "NUTSHELL_EMAIL"):
                asyncio.run(create_nutshell_lead(LeadInformation()))

    def test_request_uses_rest_api(self) -> None:
        response = MagicMock(status=200)
        response.json = AsyncMock(return_value={"users": []})
        response_context = AsyncMock()
        response_context.__aenter__.return_value = response
        session = MagicMock()
        session.request.return_value = response_context
        headers = {"Authorization": "Basic credentials"}

        result = asyncio.run(
            _nutshell_request(session, "GET", "users", headers)
        )

        session.request.assert_called_once_with(
            "GET",
            f"{NUTSHELL_API_URL}/users",
            headers=headers,
        )
        self.assertEqual(result, {"users": []})

    def test_mutating_request_is_not_retried(self) -> None:
        response_context = AsyncMock()
        response_context.__aenter__.side_effect = aiohttp.ClientConnectionError()
        session = MagicMock()
        session.request.return_value = response_context

        with self.assertRaises(aiohttp.ClientConnectionError):
            asyncio.run(_nutshell_request(session, "POST", "leads", {}))

        session.request.assert_called_once()

    def test_one_in_three_assignment_resolves_ashton(self) -> None:
        users = {
            "users": [
                {"id": "3-users", "emails": ["sky@billboardsource.com"]},
                {"id": "35-users", "emails": ["ashton@billboardsource.com"]},
            ]
        }
        with (
            patch("src.data_source.nutshell.random.randrange", return_value=0),
            patch(
                "src.data_source.nutshell._nutshell_request",
                new=AsyncMock(return_value=users),
            ),
        ):
            user_id = asyncio.run(
                _resolve_assignee_id(MagicMock(), {}, "sky@billboardsource.com")
            )

        self.assertEqual(user_id, "35-users")

    def test_contact_lookup_reuses_first_exact_email_match(self) -> None:
        contacts = {
            "contacts": [
                {"id": "wrong", "emails": [{"value": "other@example.com"}]},
                {"id": "match", "emails": [{"value": "JANE@example.com"}]},
                {"id": "later", "emails": [{"value": "jane@example.com"}]},
            ]
        }
        request = AsyncMock(return_value=contacts)

        with patch("src.data_source.nutshell._nutshell_request", new=request):
            contact_id = asyncio.run(
                _find_or_create_contact(
                    MagicMock(),
                    {},
                    LeadInformation(email="jane@example.com"),
                    None,
                )
            )

        self.assertEqual(contact_id, "match")
        request.assert_awaited_once_with(
            ANY,
            "GET",
            "contacts",
            {},
            params={"filter[email]": "jane@example.com", "page[limit]": 100},
        )

    def test_lead_information_maps_to_rest_resources(self) -> None:
        results = [
            {"users": [{"id": "3-users", "emails": ["agent@example.com"]}]},
            {"accounts": []},
            {"accounts": [{"id": "1-accounts"}]},
            {"contacts": []},
            {"contacts": [{"id": "2-contacts"}]},
            {
                "stagesets": [
                    {"id": "3-stagesets", "name": "NEW BSI Pipeline"}
                ]
            },
            {"sources": []},
            {"sources": [{"id": "5-sources"}]},
            {"leads": [{"id": "6-leads", "description": "Example Company"}]},
            {"id": "3-stagesets"},
            {"id": "8-notes"},
        ]
        session = MagicMock()
        session_context = AsyncMock()
        session_context.__aenter__.return_value = session
        lead = LeadInformation(
            name=" Jane Smith ",
            email=" jane@example.com ",
            phone=" +15551234567 ",
            business=" Example Company ",
            billboard_location=" Detroit, MI ",
            notes=" Interested in digital ",
            transcript=" I need a billboard. ",
        )

        with (
            patch.dict(
                os.environ,
                {"NUTSHELL_EMAIL": "agent@example.com", "NUTSHELL_API_KEY": "secret"},
                clear=True,
            ),
            patch(
                "src.data_source.nutshell.aiohttp.ClientSession",
                return_value=session_context,
            ),
            patch(
                "src.data_source.nutshell._nutshell_request",
                new=AsyncMock(side_effect=results),
            ) as request,
            patch("src.data_source.nutshell.random.randrange", return_value=1),
        ):
            result = asyncio.run(create_nutshell_lead(lead))

        headers = {
            "Accept": "application/json",
            "Authorization": aiohttp.encode_basic_auth(
                "agent@example.com",
                "secret",
            ),
        }
        self.assertEqual(
            request.call_args_list,
            [
                call(ANY, "GET", "users", headers),
                call(
                    ANY,
                    "GET",
                    "accounts",
                    headers,
                    params={
                        "filter[name]": "Example Company",
                        "page[limit]": 100,
                    },
                ),
                call(
                    ANY,
                    "POST",
                    "accounts",
                    headers,
                    payload={"accounts": [{"name": "Example Company"}]},
                ),
                call(
                    ANY,
                    "GET",
                    "contacts",
                    headers,
                    params={
                        "filter[email]": "jane@example.com",
                        "page[limit]": 100,
                    },
                ),
                call(
                    ANY,
                    "POST",
                    "contacts",
                    headers,
                    payload={
                        "contacts": [
                            {
                                "name": "Jane Smith",
                                "phones": [
                                    {"value": "+15551234567", "isPrimary": True}
                                ],
                                "emails": [
                                    {"value": "jane@example.com", "isPrimary": True}
                                ],
                                "links": {"accounts": ["1-accounts"]},
                            }
                        ]
                    },
                ),
                call(ANY, "GET", "stagesets", headers),
                call(
                    ANY,
                    "GET",
                    "sources",
                    headers,
                    params={"q": "AI Voice"},
                ),
                call(
                    ANY,
                    "POST",
                    "sources",
                    headers,
                    payload={"sources": [{"name": "AI Voice"}]},
                ),
                call(
                    ANY,
                    "POST",
                    "leads",
                    headers,
                    payload={
                        "leads": [
                            {
                                "description": "Example Company",
                                "links": {
                                    "owner": "3-users",
                                    "sources": ["5-sources"],
                                    "accounts": ["1-accounts"],
                                    "contacts": ["2-contacts"],
                                },
                                "customFields": {
                                    "Target Market(s) - City/State/Area": "Detroit, MI",
                                    "Notes:": "Interested in digital",
                                },
                            }
                        ]
                    },
                ),
                call(
                    ANY,
                    "POST",
                    "leads/6-leads/stageset",
                    headers,
                    payload={"stageset": "3-stagesets"},
                ),
                call(
                    ANY,
                    "POST",
                    "notes",
                    headers,
                    payload={
                        "data": {
                            "body": "--- CALL TRANSCRIPT ---\n\nI need a billboard.",
                            "links": {"parent": "6-leads"},
                        }
                    },
                ),
            ],
        )
        self.assertEqual(
            result,
            {"id": "6-leads", "description": "Example Company"},
        )


if __name__ == "__main__":
    unittest.main()
