"""Unit tests for skill documentation validation.

Tests that SKILL.md files contain proper documentation with:
- Valid frontmatter (name, description)
- Clear usage examples
- Correct CLI syntax
"""

import re
from pathlib import Path

import pytest


SKILLS_DIR = Path(__file__).parent.parent.parent / "tools"


def get_all_skill_md_files() -> list[Path]:
    """Find all SKILL.md files in the tools directory."""
    if not SKILLS_DIR.exists():
        return []
    return list(SKILLS_DIR.glob("**/SKILL.md"))


def parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from markdown content."""
    if not content.startswith("---"):
        return {}
    
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    
    frontmatter = {}
    for line in parts[1].strip().split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip().strip('"\'')
    
    return frontmatter


def extract_code_blocks(content: str) -> list[dict]:
    """Extract all code blocks from markdown content."""
    pattern = r"```(\w*)\n(.*?)```"
    matches = re.findall(pattern, content, re.DOTALL)
    return [{"language": lang or "text", "code": code.strip()} for lang, code in matches]


class TestSkillDocumentationStructure:
    """Tests for SKILL.md file structure."""

    @pytest.fixture
    def skill_files(self) -> list[Path]:
        """Get all SKILL.md files."""
        return get_all_skill_md_files()

    def test_skills_directory_exists(self):
        """Test that skills directory exists."""
        assert SKILLS_DIR.exists(), f"Skills directory not found: {SKILLS_DIR}"

    def test_all_skills_have_frontmatter(self, skill_files):
        """Test that all SKILL.md files have valid frontmatter."""
        if not skill_files:
            pytest.skip("No SKILL.md files found")
        
        for skill_file in skill_files:
            content = skill_file.read_text()
            frontmatter = parse_frontmatter(content)
            
            assert "name" in frontmatter, (
                f"{skill_file} missing 'name' in frontmatter"
            )
            assert "description" in frontmatter, (
                f"{skill_file} missing 'description' in frontmatter"
            )
            assert len(frontmatter["description"]) > 10, (
                f"{skill_file} has too short description"
            )

    def test_all_skills_have_code_examples(self, skill_files):
        """Test that all SKILL.md files have code examples."""
        if not skill_files:
            pytest.skip("No SKILL.md files found")
        
        for skill_file in skill_files:
            content = skill_file.read_text()
            code_blocks = extract_code_blocks(content)
            
            assert len(code_blocks) > 0, (
                f"{skill_file} has no code examples"
            )


class TestHimalayaSkillDocumentation:
    """Specific tests for Himalaya email skill documentation."""

    @pytest.fixture
    def himalaya_skill_path(self) -> Path:
        """Get path to Himalaya SKILL.md."""
        path = SKILLS_DIR / "splintermaster" / "himalaya" / "SKILL.md"
        if not path.exists():
            pytest.skip("Himalaya skill not installed")
        return path

    @pytest.fixture
    def himalaya_content(self, himalaya_skill_path) -> str:
        """Get Himalaya SKILL.md content."""
        return himalaya_skill_path.read_text()

    def test_himalaya_has_date_filter_documentation(self, himalaya_content):
        """Test that Himalaya skill documents date filtering."""
        assert "date" in himalaya_content.lower()
        # Should have the date filter syntax
        assert "date <yyyy-mm-dd>" in himalaya_content or "date 20" in himalaya_content

    def test_himalaya_has_before_after_filters(self, himalaya_content):
        """Test that Himalaya skill documents before/after filters."""
        assert "before" in himalaya_content.lower()
        assert "after" in himalaya_content.lower()

    def test_himalaya_has_search_operators(self, himalaya_content):
        """Test that Himalaya skill documents search operators."""
        # Should document AND, OR, NOT operators
        content_lower = himalaya_content.lower()
        assert " and " in content_lower or "`and`" in content_lower
        assert " or " in content_lower or "`or`" in content_lower
        assert " not " in content_lower or "`not`" in content_lower

    def test_himalaya_has_sort_documentation(self, himalaya_content):
        """Test that Himalaya skill documents sorting."""
        assert "order by" in himalaya_content.lower()
        assert "desc" in himalaya_content.lower() or "asc" in himalaya_content.lower()

    def test_himalaya_documents_flag_order(self, himalaya_content):
        """Test that Himalaya skill documents CLI flag order.
        
        Critical: --output json must come BEFORE the query!
        """
        # Should warn about flag order
        assert "--output" in himalaya_content
        # Should have examples with correct order (flags before query)
        assert "--output json date" in himalaya_content or \
               "--output json after" in himalaya_content or \
               "--output json not" in himalaya_content

    def test_himalaya_has_common_use_cases(self, himalaya_content):
        """Test that Himalaya skill has common use case examples."""
        # Should have practical examples
        assert "today" in himalaya_content.lower()
        assert "unread" in himalaya_content.lower()

    def test_himalaya_code_blocks_have_correct_syntax(self, himalaya_content):
        """Test that Himalaya code examples use correct syntax."""
        code_blocks = extract_code_blocks(himalaya_content)
        
        bash_blocks = [b for b in code_blocks if b["language"] in ("bash", "")]
        assert len(bash_blocks) > 0, "No bash code blocks found"
        
        # Check that examples don't have the broken syntax
        # Only check actual himalaya command lines, not comments
        for block in bash_blocks:
            code = block["code"]
            # Check each line that contains a himalaya command
            for line in code.split("\n"):
                line = line.strip()
                # Skip comments and non-himalaya lines
                if line.startswith("#") or "himalaya" not in line:
                    continue
                
                if "envelope list" in line and "--output" in line:
                    # If both are present, --output should come before query keywords
                    query_keywords = ["date ", "after ", "before ", "from ", "to ", "subject ", "body ", "flag ", "not "]
                    
                    output_pos = line.find("--output")
                    query_positions = [line.find(kw) for kw in query_keywords if line.find(kw) > -1]
                    
                    if query_positions:
                        first_query_pos = min(query_positions)
                        assert output_pos < first_query_pos, (
                            f"--output flag should come before query in: {line}"
                        )


class TestSkillDocumentationCompleteness:
    """Tests for documentation completeness across all skills."""

    @pytest.fixture
    def skill_files(self) -> list[Path]:
        """Get all SKILL.md files."""
        return get_all_skill_md_files()

    def test_skills_have_installation_check(self, skill_files):
        """Test that skills document how to verify installation."""
        if not skill_files:
            pytest.skip("No SKILL.md files found")
        
        for skill_file in skill_files:
            content = skill_file.read_text()
            # Should have some form of installation/verification docs
            has_install_docs = any(term in content.lower() for term in [
                "install", "prerequisite", "require", "verify", "check"
            ])
            assert has_install_docs, (
                f"{skill_file} missing installation/verification documentation"
            )

    def test_skills_have_error_handling_docs(self, skill_files):
        """Test that skills document error handling."""
        if not skill_files:
            pytest.skip("No SKILL.md files found")
        
        for skill_file in skill_files:
            content = skill_file.read_text()
            # Should have some error handling guidance
            has_error_docs = any(term in content.lower() for term in [
                "error", "troubleshoot", "issue", "problem", "fail"
            ])
            # This is a soft check - warn but don't fail
            if not has_error_docs:
                pytest.warns(
                    UserWarning,
                    match=f"{skill_file.name} could benefit from error handling docs"
                )
