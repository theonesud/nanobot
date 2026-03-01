from nanobot.utils.helpers import ensure_dir, safe_filename, timestamp


class TestEnsureDir:
    def test_ensure_dir_creates_directory(self, temp_workspace):
        new_dir = temp_workspace / "new_dir"
        result = ensure_dir(new_dir)
        assert result.exists()
        assert result.is_dir()

    def test_ensure_dir_existing_directory(self, temp_workspace):
        new_dir = temp_workspace / "existing"
        new_dir.mkdir()
        result = ensure_dir(new_dir)
        assert result.exists()

    def test_ensure_dir_nested(self, temp_workspace):
        nested = temp_workspace / "a" / "b" / "c"
        result = ensure_dir(nested)
        assert result.exists()
        assert result.is_dir()


class TestSafeFilename:
    def test_safe_filename_simple(self):
        result = safe_filename("test_file.txt")
        assert result == "test_file.txt"

    def test_safe_filename_with_unsafe_chars(self):
        result = safe_filename("file<name>.txt")
        assert "<" not in result
        assert ">" not in result

    def test_safe_filename_strips(self):
        result = safe_filename("  filename  ")
        assert result == "filename"


class TestTimestamp:
    def test_timestamp_format(self):
        ts = timestamp()
        assert "T" in ts
        assert ":" in ts
