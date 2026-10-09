"""Delimited file-block format used by engineer/QA agents (more reliable than code-in-JSON)."""
import pytest

from services.agents.specialists import parse_file_blocks, SYSTEM_PROMPTS, check_python_syntax
from services.agents.contracts import AgentRole


def test_parses_files_notes_and_uncertainty():
    text = ("=== FILE: app/x.py ===\nx = '=== not an end marker'\n=== END FILE ===\n"
            "=== FILE: tests/test_x.py ===\ndef test_x():\n    assert True\n=== END FILE ===\n"
            "=== NOTES ===\nimplemented\n=== UNCERTAINTY ===\nnone\n")
    work = parse_file_blocks(text)
    assert [f.path for f in work.files] == ["app/x.py", "tests/test_x.py"]
    assert work.files[0].content == "x = '=== not an end marker'\n"
    assert work.notes == "implemented" and work.uncertainty == "none"
    check_python_syntax(work)


@pytest.mark.parametrize("text", ["", "plain prose answer", "=== FILE: a.py ===\nunterminated",
                                  "=== FILE: a.py ===\nx = 1\n=== END FILE ===\n=== FILE: b.py ===\ny = 2"])
def test_malformed_output_is_rejected(text):
    with pytest.raises(ValueError):
        parse_file_blocks(text)


def test_prompts_ask_for_the_block_format():
    for role in (AgentRole.ENGINEER, AgentRole.QA):
        assert "=== FILE: <relative path> ===" in SYSTEM_PROMPTS[role] and "{FILE_FORMAT}" not in SYSTEM_PROMPTS[role]
