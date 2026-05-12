"""Contract tests for the pure helpers in gagent_client.invoke.

The full invoke() pipeline reaches AWS and is exercised by integration
tests; these cover the pure-function helpers that don't touch AWS.
"""

from __future__ import annotations

from gagent_client.identity import Persona
from gagent_client.invoke import _with_persona_context


def _persona(role: str, service_region: str | None = None) -> Persona:
    return Persona(
        role=role,
        service_region=service_region,
        role_arn=f"arn:aws:iam::123:role/{role}",
    )


class TestWithPersonaContext:
    def test_no_service_region_still_prefixes_role(self):
        out = _with_persona_context("hello", _persona("dispatcher"))
        assert out.startswith("[Persona context: you are acting as dispatcher.")
        assert out.endswith("hello")

    def test_service_region_is_mentioned_and_instructions_explain_my_region(self):
        out = _with_persona_context(
            "show me customers in my region",
            _persona("technician_lead", service_region="tempe-mesa"),
        )
        assert "tempe-mesa" in out
        assert "my region" in out
        assert "do not ask the user for the region" in out
        # The raw question survives at the end after a blank-line separator.
        assert out.endswith("\n\nshow me customers in my region")

    def test_owner_with_no_region_does_not_mention_region(self):
        out = _with_persona_context("show me revenue", _persona("owner"))
        assert "service region" not in out.lower()
        assert out.endswith("show me revenue")
