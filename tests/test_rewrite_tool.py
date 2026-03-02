
import pytest

from nanobot.agent.tools.rewrite import RewriteCodeTool


class TestRewriteCodeTool:
    @pytest.fixture
    def tool(self, temp_workspace):
        return RewriteCodeTool(workspace=temp_workspace)

    @pytest.mark.asyncio
    async def test_rewrite_function(self, tool, temp_workspace):
        f = temp_workspace / "code.py"
        f.write_text("def hello():\n    print('hi')\n\ndef other():\n    pass")

        new_code = "def hello():\n    print('hello world')"
        result = await tool.execute("code.py", "hello", new_code)

        assert "Successfully" in result
        content = f.read_text()
        assert "hello world" in content
        assert "def other():" in content

    @pytest.mark.asyncio
    async def test_rewrite_class_method(self, tool, temp_workspace):
        f = temp_workspace / "cls.py"
        f.write_text("class A:\n    def m(self):\n        return 1")

        new_code = "def m(self):\n    return 2"
        result = await tool.execute("cls.py", "A.m", new_code)

        assert "Successfully" in result
        content = f.read_text()
        assert "return 2" in content
        assert "class A:" in content

    @pytest.mark.asyncio
    async def test_symbol_not_found(self, tool, temp_workspace):
        f = temp_workspace / "err.py"
        f.write_text("def x(): pass")

        result = await tool.execute("err.py", "y", "def y(): pass")
        assert "Error" in result
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_nested_class(self, tool, temp_workspace):
        f = temp_workspace / "nested.py"
        f.write_text("class Outer:\n    class Inner:\n        def run(self):\n            pass")

        new_code = "def run(self):\n    print('nested')"
        result = await tool.execute("nested.py", "Outer.Inner.run", new_code)

        assert "Successfully" in result
        assert "print('nested')" in f.read_text()
