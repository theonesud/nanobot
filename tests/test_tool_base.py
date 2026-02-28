"""Tests for agent/tools/base.py — Tool base class."""

import pytest
from typing import Any

from nanobot.agent.tools.base import Tool


class ConcreteToolSimple(Tool):
    """A minimal concrete tool for testing."""

    @property
    def name(self) -> str:
        return "simple_tool"

    @property
    def description(self) -> str:
        return "A simple test tool."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["text"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return f"executed: {kwargs.get('text')}"


class TestToolSchema:
    def test_to_schema_basic_structure(self):
        tool = ConcreteToolSimple()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert "function" in schema
        assert schema["function"]["name"] == "simple_tool"
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]

    def test_to_schema_includes_parameters(self):
        tool = ConcreteToolSimple()
        schema = tool.to_schema()
        params = schema["function"]["parameters"]
        assert "text" in params["properties"]

    def test_to_schema_description(self):
        tool = ConcreteToolSimple()
        schema = tool.to_schema()
        assert schema["function"]["description"] == "A simple test tool."


class TestToolValidateParams:
    def test_valid_params_no_errors(self):
        tool = ConcreteToolSimple()
        errors = tool.validate_params({"text": "hello"})
        assert errors == []

    def test_missing_required_param(self):
        tool = ConcreteToolSimple()
        errors = tool.validate_params({})
        assert len(errors) > 0
        assert any("text" in e for e in errors)

    def test_wrong_type_string_for_integer(self):
        tool = ConcreteToolSimple()
        errors = tool.validate_params({"text": "hello", "count": "not-an-int"})
        assert len(errors) > 0

    def test_correct_types_no_errors(self):
        tool = ConcreteToolSimple()
        errors = tool.validate_params({"text": "hello", "count": 5})
        assert errors == []

    def test_extra_params_allowed(self):
        """Extra parameters not in schema should not be flagged, they might be context kwargs."""
        tool = ConcreteToolSimple()
        errors = tool.validate_params({"text": "hello", "extra_context": "whatever"})
        assert errors == []  # extra is fine

    @pytest.mark.asyncio
    async def test_execute_basic(self):
        tool = ConcreteToolSimple()
        result = await tool.execute(text="world")
        assert "executed" in result
        assert "world" in result
