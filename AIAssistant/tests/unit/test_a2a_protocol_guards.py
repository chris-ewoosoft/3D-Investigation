"""Guard-path coverage for the A2A protocol adapter's local-only edges.

These tests pin ``AgentSkill.__repr__`` (the debugging aid a reviewer reads
when a card looks wrong) and the registry lookup behind the supervisor's
injected port (ADR 0002).
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ai_assistant.adapters import a2a_client, a2a_protocol
from ai_assistant.adapters.a2a_payloads import build_agent_card as build_agent_card_payload
from ai_assistant.adapters.a2a_protocol import (
    AgentCard,
    AgentSkill,
    RemoteAgent,
    RemoteAgentRegistry,
    build_agent_card,
)


class AgentSkillTests(unittest.TestCase):
    def test_repr_shows_the_identity_not_the_body(self):
        skill = a2a_protocol.AgentSkill("code", "Code Specialist", "writes code", ["py"])
        self.assertEqual(repr(skill), "AgentSkill(id='code', name='Code Specialist')")

    def test_a_skill_without_tags_advertises_none(self):
        skill = a2a_protocol.AgentSkill("chatbot", "Chatbot", "chats")
        self.assertEqual(skill.tags, [])


class RemoteSpecialistLookupTests(unittest.TestCase):
    """The composition root's resolver returns a URL only for an advertised skill."""

    def setUp(self) -> None:
        self.registry = a2a_protocol.RemoteAgentRegistry(bearer_token="peer-token")

    def test_a_discovered_advertisement_resolves_to_its_endpoint(self):
        self.registry._agents["http://remote.test/a2a"] = SimpleNamespace(
            skills=["chatbot", "code"],
        )
        self.registry._agents["http://other.test/a2a"] = SimpleNamespace(skills=["research"])
        self.assertEqual(
            self.registry.resolve("research"), "http://other.test/a2a",
        )

    def test_an_unadvertised_specialist_delegates_locally(self):
        self.registry._agents["http://remote.test/a2a"] = SimpleNamespace(skills=["chatbot"])
        self.assertIsNone(self.registry.resolve("verification"))

    def test_an_empty_registry_delegates_locally(self):
        self.assertIsNone(self.registry.resolve("chatbot"))

    def test_find_returns_the_agent_not_only_its_url(self):
        agent = SimpleNamespace(skills=["code"])
        self.registry._agents["http://remote.test/a2a"] = agent
        self.assertIs(self.registry.find("code"), agent)
        self.assertIsNone(self.registry.find("research"))



# ── Card construction pins (moved from test_coverage_boost4) ───

class AgentCardTests(unittest.TestCase):
    """Coverage for Agent Card construction and serialisation."""

    def test_build_agent_card_has_skills(self) -> None:
        card = build_agent_card(base_url="https://example.com")
        self.assertIsInstance(card, AgentCard)
        self.assertEqual(card.url, "https://example.com/a2a")
        self.assertGreater(len(card.skills), 0)
        self.assertIsInstance(card.skills[0], AgentSkill)

    def test_dataclass_card_matches_served_payload(self) -> None:
        """The dataclass view and the served wire dict share one source."""
        capabilities = ["research", "supervisor"]
        card = build_agent_card(base_url="https://example.com", capabilities=capabilities)
        payload = build_agent_card_payload(
            agent_name="3D-Reconstruction AI Agent Platform",
            agent_version="1.0.0",
            base_url="https://example.com",
            capabilities=capabilities,
        )
        self.assertEqual(card.url, payload["url"])
        self.assertEqual(card.protocolVersion, payload["protocolVersion"])
        self.assertEqual(
            [skill.id for skill in card.skills],
            [skill["id"] for skill in payload["skills"]],
        )

    def test_agent_card_serialisation_round_trip(self) -> None:
        card = AgentCard(
            name="Test", description="d", url="https://x",
            skills=[AgentSkill(id="s", name="S", description="desc", tags=["t"])],
        )
        payload = json.loads(card.to_json())
        self.assertEqual(payload["name"], "Test")
        self.assertEqual(card.to_dict()["url"], "https://x")


# ── Remote discovery paths (moved from test_coverage_boost4) ───

class RemoteDiscoveryTests(unittest.TestCase):
    """Coverage for remote agent discovery on an owned registry instance."""

    def test_discovery_without_an_outbound_token_is_fail_closed(self) -> None:
        registry = RemoteAgentRegistry()
        with patch("ai_assistant.adapters.a2a_protocol.urlopen") as mock_urlopen:
            result = registry.discover(["https://remote.example.com"])
        self.assertEqual(result, {})
        mock_urlopen.assert_not_called()

    def test_discover_no_urls_returns_snapshot(self) -> None:
        registry = RemoteAgentRegistry(bearer_token="peer-token")
        with patch.object(
            a2a_protocol, "a2a_trusted_endpoints", return_value=frozenset()
        ):
            result = registry.discover()
        self.assertEqual(result, {})

    def test_discover_defaults_to_the_deployment_allowlist(self) -> None:
        registry = RemoteAgentRegistry(bearer_token="peer-token")
        with patch.object(
            a2a_protocol, "a2a_trusted_endpoints", return_value=frozenset({"https://remote.test"})
        ), patch("ai_assistant.adapters.a2a_protocol.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value.read.return_value = b'{"name":"R","skills":[]}'
            mock_urlopen.return_value = mock_resp
            registry.discover()
        self.assertIn("https://remote.test", mock_urlopen.call_args.args[0].full_url)

    def test_discover_success_populates_registry(self) -> None:
        payload = {
            "name": "Remote",
            "url": "https://remote.example.com",
            "skills": [{"id": "supervisor", "name": "Supervisor"}, {"id": "", "name": "ignored"}],
        }
        registry = RemoteAgentRegistry(bearer_token="peer-token")
        with patch("ai_assistant.adapters.a2a_protocol.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
            mock_urlopen.return_value = mock_resp
            result = registry.discover(["https://remote.example.com"])

        self.assertIn("https://remote.example.com", result)
        self.assertIn("supervisor", result["https://remote.example.com"].skills)

    def test_discover_failure_is_swallowed(self) -> None:
        registry = RemoteAgentRegistry(bearer_token="peer-token")
        with patch("ai_assistant.adapters.a2a_protocol.urlopen", side_effect=OSError("unreachable")):
            result = registry.discover(["https://remote.example.com"])
        self.assertEqual(result, {})

    def test_snapshot_returns_a_copy(self) -> None:
        registry = RemoteAgentRegistry(bearer_token="peer-token")
        registry._agents["u"] = RemoteAgent(url="u", card={}, skills={})
        snapshot = registry.snapshot()
        snapshot.pop("u")
        self.assertIn("u", registry.snapshot())

    def test_discover_uses_well_known_url(self) -> None:
        base = "https://remote.example.com"
        registry = RemoteAgentRegistry(bearer_token="peer-token")
        with patch("ai_assistant.adapters.a2a_protocol.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value.read.return_value = b'{"name":"R","skills":[]}'
            mock_urlopen.return_value = mock_resp
            registry.discover([base])
        request = mock_urlopen.call_args.args[0]
        self.assertIn(".well-known/agent.json", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer peer-token")

    def test_discover_refuses_non_http_urls(self) -> None:
        registry = RemoteAgentRegistry(bearer_token="peer-token")
        with patch("ai_assistant.adapters.a2a_protocol.urlopen") as mock_urlopen:
            result = registry.discover(["ftp://evil.example.com", "not-a-url"])
        self.assertEqual(result, {})
        mock_urlopen.assert_not_called()

    def test_discovery_uses_the_no_redirect_opener(self) -> None:
        """Discovery and dispatch must not disagree about redirects.

        The dispatch path refuses a 30x on purpose — following it would turn
        one compromised peer into SSRF against the host's network — while
        discovery used the *default* urllib opener and therefore followed
        redirects. Discovery is the leg that runs at startup against a peer
        that has not been vetted yet, so it is the leg that must not be the
        weaker one.
        """
        self.assertIs(
            a2a_protocol.urlopen,
            a2a_client.urlopen,
            "discovery must use the same no-redirect opener as dispatch",
        )

    def test_a_redirecting_peer_does_not_steer_the_discovery_fetch(self) -> None:
        """A 30x during discovery must not be followed to another host."""
        registry = RemoteAgentRegistry(bearer_token="peer-token")
        with patch("ai_assistant.adapters.a2a_protocol.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value.read.return_value = b'{"name":"R","skills":[]}'
            mock_urlopen.return_value = mock_resp
            registry.discover(["https://remote.example.com"])
        requested = mock_urlopen.call_args.args[0].full_url
        self.assertTrue(requested.startswith("https://remote.example.com"))

    def test_discover_refuses_oversized_cards(self) -> None:
        payload = {"name": "R", "skills": [], "pad": "x" * 64}
        body = json.dumps(payload).encode("utf-8")
        registry = RemoteAgentRegistry(bearer_token="peer-token")
        with patch("ai_assistant.adapters.a2a_protocol.urlopen") as mock_urlopen, patch.object(
            a2a_protocol, "_DISCOVERY_MAX_BYTES", 10
        ):
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value.read.return_value = body
            mock_urlopen.return_value = mock_resp
            result = registry.discover(["https://remote.example.com"])
        # Refused by the size gate, not parsed: without the gate this valid
        # JSON would populate the registry and the mutant would survive.
        self.assertEqual(result, {})

    def test_discover_refuses_invalid_json_and_non_object(self) -> None:
        for body in (b"not-json{", b'["not", "an", "object"]'):
            registry = RemoteAgentRegistry(bearer_token="peer-token")
            with self.subTest(body=body), patch("ai_assistant.adapters.a2a_protocol.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.__enter__.return_value.read.return_value = body
                mock_urlopen.return_value = mock_resp
                result = registry.discover(["https://remote.example.com"])
            self.assertEqual(result, {})

    def test_discover_keeps_base_when_peer_advertises_foreign_host(self) -> None:
        payload = {
            "name": "Remote",
            "url": "https://evil.example.com/a2a",
            "skills": [{"id": "code", "name": "Code"}],
        }
        registry = RemoteAgentRegistry(bearer_token="peer-token")
        with patch("ai_assistant.adapters.a2a_protocol.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
            mock_urlopen.return_value = mock_resp
            result = registry.discover(["https://remote.example.com"])
        self.assertEqual(result["https://remote.example.com"].url, "https://remote.example.com")
        self.assertIn("code", result["https://remote.example.com"].skills)

    def test_discover_rejects_a_lookalike_host_prefix(self) -> None:
        payload = {
            "name": "Remote",
            "url": "https://remote.example.com.evil/a2a",
            "skills": [{"id": "code", "name": "Code"}],
        }
        registry = RemoteAgentRegistry(bearer_token="peer-token")
        with patch("ai_assistant.adapters.a2a_protocol.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
            mock_urlopen.return_value = mock_resp
            result = registry.discover(["https://remote.example.com"])
        self.assertEqual(result["https://remote.example.com"].url, "https://remote.example.com")
        self.assertIn("code", result["https://remote.example.com"].skills)

    def test_discover_keeps_base_even_when_card_advertises_a_subpath(self) -> None:
        payload = {
            "name": "Remote",
            "url": "https://remote.example.com/a2a",
            "skills": [{"id": "code", "name": "Code"}, "not-a-dict"],
        }
        registry = RemoteAgentRegistry(bearer_token="peer-token")
        with patch("ai_assistant.adapters.a2a_protocol.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
            mock_urlopen.return_value = mock_resp
            result = registry.discover(["https://remote.example.com"])
        self.assertEqual(result["https://remote.example.com"].url, "https://remote.example.com")

if __name__ == "__main__":
    unittest.main()
