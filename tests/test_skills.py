"""Tests for the SWE skills store (SKILL.md files under ./skills/<domain>/)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sqwakvox.domains.swe import skills as skills_store


@pytest.fixture
def skills_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the skills root at a temp dir for the duration of the test."""
    monkeypatch.setenv("SQWAKVOX_SKILLS_DIR", str(tmp_path))
    return tmp_path


def test_create_and_list_skill(skills_dir: Path) -> None:
    created = skills_store.create_skill(
        "my-skill",
        "Use for X",
        "# My Skill\n\nDo the thing.",
        domain_id="swe",
    )
    assert created == skills_dir / "swe" / "my-skill" / "SKILL.md"
    assert created.exists()

    listed = skills_store.list_skills("swe")
    assert [s["name"] for s in listed] == ["my-skill"]
    assert listed[0]["description"] == "Use for X"


def test_created_skill_has_standard_frontmatter(skills_dir: Path) -> None:
    skills_store.create_skill("fmt", "desc", "Body here.", domain_id="swe")
    text = (skills_dir / "swe" / "fmt" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: fmt\ndescription: 'desc'\n---")
    assert "Body here." in text


def test_read_update_delete_skill(skills_dir: Path) -> None:
    del skills_dir  # fixture: points SQWAKVOX_SKILLS_DIR at tmp_path
    skills_store.create_skill("old", "d", "v1", domain_id="swe")
    assert "v1" in (skills_store.read_skill("old", "swe") or "")

    skills_store.update_skill("old", "d2", "v2", domain_id="swe")
    content = skills_store.read_skill("old", "swe") or ""
    assert "v2" in content

    assert skills_store.delete_skill("old", "swe") is True
    assert skills_store.read_skill("old", "swe") is None
    assert skills_store.delete_skill("old", "swe") is False


def test_search_skills_matches_name_and_description(skills_dir: Path) -> None:
    del skills_dir  # fixture: points SQWAKVOX_SKILLS_DIR at tmp_path
    skills_store.create_skill("postgres-role", "read-only db role", "body", domain_id="swe")
    skills_store.create_skill("git-worktree", "parallel agents", "body", domain_id="swe")

    assert [s["name"] for s in skills_store.search_skills("read-only", "swe")] == ["postgres-role"]
    assert [s["name"] for s in skills_store.search_skills("GIT", "swe")] == ["git-worktree"]
    assert skills_store.search_skills("zzz", "swe") == []


def test_skill_name_validation(skills_dir: Path) -> None:
    del skills_dir  # fixture: points SQWAKVOX_SKILLS_DIR at tmp_path
    with pytest.raises(ValueError, match="Invalid skill name"):
        skills_store.create_skill("Bad Name!", "d", "body", domain_id="swe")


def test_skill_rejects_secrets(skills_dir: Path) -> None:
    del skills_dir  # fixture: points SQWAKVOX_SKILLS_DIR at tmp_path
    with pytest.raises(ValueError, match="secrets"):
        skills_store.create_skill("leaky", "d", "key: sk-ABCDEFGHIJKLMNOPQRST", domain_id="swe")


def test_domains_are_isolated_directories(skills_dir: Path) -> None:
    skills_store.create_skill("swe-only", "d", "body", domain_id="swe")
    assert skills_store.list_skills("financial") == []
    assert (skills_dir / "swe" / "swe-only" / "SKILL.md").exists()


def test_skills_root_respects_cwd_default() -> None:
    # Without the env override the root is ./skills/<domain>.
    root = skills_store.skills_root("swe")
    assert root.parts[-2:] == ("skills", "swe")
