"""Comprehensive tests for web tools (web_search and web_fetch)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.tools.web import (
    WebFetchTool,
    WebSearchTool,
    _normalize,
    _strip_tags,
    _validate_url,
)


class TestStripTags:
    def test_removes_script(self):
        html = "<script>alert('xss')</script>hello"
        assert "script" not in _strip_tags(html)
        assert "hello" in _strip_tags(html)

    def test_removes_style(self):
        html = "<style>.x{color:red}</style>text"
        assert "color" not in _strip_tags(html)
        assert "text" in _strip_tags(html)

    def test_removes_generic_tags(self):
        html = "<p>paragraph <b>bold</b></p>"
        result = _strip_tags(html)
        assert "<" not in result
        assert "paragraph" in result
        assert "bold" in result

    def test_decodes_entities(self):
        result = _strip_tags("&lt;div&gt;")
        assert "&lt;" not in result
        assert "<div>" in result


class TestNormalize:
    def test_collapses_spaces(self):
        result = _normalize("foo   bar")
        assert result == "foo bar"

    def test_collapses_excess_newlines(self):
        result = _normalize("line1\n\n\n\nline2")
        assert result == "line1\n\nline2"

    def test_strips_whitespace(self):
        result = _normalize("  hello  ")
        assert result == "hello"


class TestValidateUrl:
    def test_valid_http(self):
        valid, _ = _validate_url("http://example.com")
        assert valid

    def test_valid_https(self):
        valid, _ = _validate_url("https://example.com/path?q=1")
        assert valid

    def test_rejects_ftp(self):
        valid, msg = _validate_url("ftp://example.com")
        assert not valid
        assert "ftp" in msg

    def test_rejects_missing_domain(self):
        valid, msg = _validate_url("https://")
        assert not valid

    def test_rejects_non_url(self):
        valid, msg = _validate_url("not-a-url")
        assert not valid

    def test_rejects_empty_string(self):
        valid, _ = _validate_url("")
        assert not valid


class TestWebSearchTool:
    def test_schema_name(self):
        assert WebSearchTool().name == "web_search"

    def test_schema_required(self):
        assert "query" in WebSearchTool().parameters["required"]

    @pytest.mark.asyncio
    async def test_no_api_key_returns_error(self):
        tool = WebSearchTool(api_key=None)
        with patch.dict("os.environ", {}, clear=True):
            result = await tool.execute("python")
        assert "Error" in result
        assert "API key" in result

    @pytest.mark.asyncio
    async def test_with_api_key_calls_brave(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "web": {
                "results": [
                    {
                        "title": "Python.org",
                        "url": "https://python.org",
                        "description": "Python language",
                    }
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("nanobot.agent.tools.web.httpx.AsyncClient", return_value=mock_client):
            tool = WebSearchTool(api_key="test-key")
            result = await tool.execute("python")

        assert "Python.org" in result
        assert "https://python.org" in result

    @pytest.mark.asyncio
    async def test_no_results_message(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"web": {"results": []}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("nanobot.agent.tools.web.httpx.AsyncClient", return_value=mock_client):
            tool = WebSearchTool(api_key="test-key")
            result = await tool.execute("xyzzy_not_a_real_query")

        assert "No results" in result

    @pytest.mark.asyncio
    async def test_count_capped_at_10(self):
        """count parameter should be capped at 10."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"web": {"results": []}}
        mock_response.raise_for_status = MagicMock()

        captured_params = {}

        async def fake_get(url, params=None, headers=None, timeout=None):
            captured_params.update(params or {})
            return mock_response

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = fake_get

        with patch("nanobot.agent.tools.web.httpx.AsyncClient", return_value=mock_client):
            tool = WebSearchTool(api_key="test-key")
            await tool.execute("test", count=50)

        assert captured_params.get("count") == 10

    @pytest.mark.asyncio
    async def test_api_error_returns_error_string(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        with patch("nanobot.agent.tools.web.httpx.AsyncClient", return_value=mock_client):
            tool = WebSearchTool(api_key="test-key")
            result = await tool.execute("test")

        assert "Error" in result

    @pytest.mark.asyncio
    async def test_api_key_from_env(self):
        with patch.dict("os.environ", {"BRAVE_API_KEY": "env-key"}):
            tool = WebSearchTool()
            assert tool.api_key == "env-key"


class TestWebFetchTool:
    def test_schema_name(self):
        assert WebFetchTool().name == "web_fetch"

    def test_schema_required_url(self):
        assert "url" in WebFetchTool().parameters["required"]

    @pytest.mark.asyncio
    async def test_invalid_url_returns_error(self):
        tool = WebFetchTool()
        result = await tool.execute("ftp://bad-scheme.com")
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_fetch_html_content(self):
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = (
            "<html><head><title>Test Page</title></head><body><p>Hello world</p></body></html>"
        )
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com"
        mock_response.json = MagicMock(side_effect=ValueError)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("nanobot.agent.tools.web.httpx.AsyncClient", return_value=mock_client):
            tool = WebFetchTool()
            result = await tool.execute("https://example.com")

        data = json.loads(result)
        assert "text" in data
        assert "Hello world" in data["text"] or "Test Page" in data["text"]

    @pytest.mark.asyncio
    async def test_fetch_json_content(self):
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = '{"key": "value"}'
        mock_response.json.return_value = {"key": "value"}
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://api.example.com/data"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("nanobot.agent.tools.web.httpx.AsyncClient", return_value=mock_client):
            tool = WebFetchTool()
            result = await tool.execute("https://api.example.com/data")

        data = json.loads(result)
        assert data["extractor"] == "json"
        assert "key" in data["text"]

    @pytest.mark.asyncio
    async def test_fetch_truncates_long_content(self):
        long_text = "A" * 60000
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = long_text
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com"
        mock_response.json = MagicMock(side_effect=ValueError)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("nanobot.agent.tools.web.httpx.AsyncClient", return_value=mock_client):
            tool = WebFetchTool(max_chars=50000)
            result = await tool.execute("https://example.com")

        data = json.loads(result)
        assert data["truncated"] is True
        assert len(data["text"]) <= 50000

    @pytest.mark.asyncio
    async def test_fetch_network_error(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))

        with patch("nanobot.agent.tools.web.httpx.AsyncClient", return_value=mock_client):
            tool = WebFetchTool()
            result = await tool.execute("https://example.com")

        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_fetch_text_mode(self):
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = "<html><body><p>Hello</p></body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com"
        mock_response.json = MagicMock(side_effect=ValueError)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("nanobot.agent.tools.web.httpx.AsyncClient", return_value=mock_client):
            tool = WebFetchTool()
            result = await tool.execute("https://example.com", extract_mode="text")

        data = json.loads(result)
        assert "Hello" in data["text"]


class TestWebFetchMarkdownConversion:
    """Test the _to_markdown helper."""

    def test_converts_headings(self):
        tool = WebFetchTool()
        html = "<h1>Title</h1><h2>Section</h2>"
        md = tool._to_markdown(html)
        assert "# Title" in md
        assert "## Section" in md

    def test_converts_links(self):
        tool = WebFetchTool()
        html = '<a href="https://python.org">Python</a>'
        md = tool._to_markdown(html)
        assert "[Python](https://python.org)" in md

    def test_converts_list_items(self):
        tool = WebFetchTool()
        html = "<ul><li>item one</li><li>item two</li></ul>"
        md = tool._to_markdown(html)
        assert "- item one" in md
        assert "- item two" in md
