import json
import os
import re
import shutil
from pathlib import Path

import yaml

BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"


class SkillsLoader:
    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        skills = []
        if self.workspace_skills.exists():
            for skill_dir in self.workspace_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists():
                        skills.append(
                            {"name": skill_dir.name, "path": str(skill_file), "source": "workspace"}
                        )
        if self.builtin_skills and self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists() and (
                        not any((s["name"] == skill_dir.name for s in skills))
                    ):
                        skills.append(
                            {"name": skill_dir.name, "path": str(skill_file), "source": "builtin"}
                        )
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills

    def load_skill(self, name: str) -> str | None:
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")
        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")
        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")
        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def escape_xml(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<skills>"]
        for s in all_skills:
            name = escape_xml(s["name"])
            path = s["path"]
            desc = escape_xml(self._get_skill_description(s["name"]))
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)
            lines.append(f'  <skill available="{str(available).lower()}">')
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")
            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")
            lines.append("  </skill>")
        lines.append("</skills>")
        return "\n".join(lines)

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        missing = []
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)

    def _get_skill_meta(self, name: str) -> dict:
        if not hasattr(self, "_meta_cache"):
            self._meta_cache = {}
        if name in self._meta_cache:
            return self._meta_cache[name]
        meta = self.get_skill_metadata(name) or {}
        res = self._parse_nanobot_metadata(meta.get("metadata", ""))
        self._meta_cache[name] = res
        return res

    def _get_skill_description(self, name: str) -> str:
        if not hasattr(self, "_desc_cache"):
            self._desc_cache = {}
        if name in self._desc_cache:
            return self._desc_cache[name]
        meta = self.get_skill_metadata(name)
        res = meta.get("description", name) if meta else name
        self._desc_cache[name] = res
        return res

    def _strip_frontmatter(self, content: str) -> str:
        if content.startswith("---"):
            match = re.match("^---\\n.*?\\n---\\n", content, re.DOTALL)
            if match:
                return content[match.end() :].strip()
        return content

    def _parse_nanobot_metadata(self, raw: str) -> dict:
        try:
            data = json.loads(raw)
            return data.get("nanobot", data.get("openclaw", {})) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True

    def get_skill_metadata(self, name: str) -> dict | None:
        content = self.load_skill(name)
        if not content:
            return None
        if content.startswith("---"):
            match = re.match("^---\\n(.*?)\\n---", content, re.DOTALL)
            if match:
                try:
                    return yaml.safe_load(match.group(1)) or {}
                except ImportError:
                    metadata = {}
                    for line in match.group(1).split("\n"):
                        if ":" in line:
                            parts = line.split(":", 1)
                            if len(parts) == 2:
                                metadata[parts[0].strip()] = parts[1].strip().strip("\"'")
                    return metadata
        return None
