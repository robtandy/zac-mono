"""Tests for the ReadTool and EditTool."""
import tempfile
from pathlib import Path

import pytest

from agent.tools import ReadTool, EditTool, ToolResult


@pytest.fixture
def temp_file():
    """Fixture to provide the path to the long test file."""
    return str(Path(__file__).parent / "long_file.py")


@pytest.mark.asyncio
async def test_read_tool(temp_file):
    """Test that ReadTool outputs lines in line:hash|content format."""
    read_tool = ReadTool()
    result = await read_tool.execute({"file_path": temp_file})
    
    assert not result.is_error
    lines = result.output.split("\n")
    
    # Check the format of each line
    for line in lines:
        assert "|" in line, f"Line '{line}' does not contain '|'"
        line_num, rest = line.split(":", 1)
        hash_val, content = rest.split("|", 1)
        
        assert line_num.isdigit(), f"Line number '{line_num}' is not a digit"
        assert len(hash_val) == 2, f"Hash '{hash_val}' is not 2 characters long"
        assert content is not None, f"Content is missing in line '{line}'"
    
    # Check the content of the first line
    assert "100-line Python file" in lines[0]


@pytest.mark.asyncio
async def test_read_tool_file_not_found():
    """Test that ReadTool handles file not found errors."""
    read_tool = ReadTool()
    result = await read_tool.execute({"file_path": "/nonexistent/file.txt"})
    
    assert result.is_error
    assert "File not found" in result.output


@pytest.mark.asyncio
async def test_edit_tool_single_line(temp_file):
    """Test that EditTool can edit a single line using a content hash."""
    read_tool = ReadTool()
    edit_tool = EditTool()
    
    # Read the file to get the hashes
    result = await read_tool.execute({"file_path": temp_file})
    assert not result.is_error
    
    lines = result.output.split("\n")
    # Find the line with "print" and extract its hash
    print_line = None
    for line in lines:
        if "print" in line:
            print_line = line
            break
    
    assert print_line is not None, "Line with 'print' not found"
    line_num, rest = print_line.split(":", 1)
    hash_val, _ = rest.split("|", 1)
    
    # Edit the line
    new_text = "    print(\"Hello, test!\")"
    result = await edit_tool.execute({
        "file_path": temp_file,
        "hash": f"{line_num}:{hash_val}",
        "new_text": new_text,
    })
    
    assert not result.is_error, f"Edit failed: {result.output}"
    
    # Verify the edit
    result = await read_tool.execute({"file_path": temp_file})
    assert not result.is_error
    assert new_text in result.output


@pytest.mark.asyncio
async def test_edit_tool_range(temp_file):
    """Test that EditTool can edit a range of lines using content hashes."""
    read_tool = ReadTool()
    edit_tool = EditTool()
    
    # Read the file to get the hashes
    result = await read_tool.execute({"file_path": temp_file})
    assert not result.is_error
    
    lines = result.output.split("\n")
    # Extract the first and last line hashes
    first_line = lines[0]
    last_line = lines[-1]
    
    first_line_num, first_rest = first_line.split(":", 1)
    first_hash, _ = first_rest.split("|", 1)
    
    last_line_num, last_rest = last_line.split(":", 1)
    last_hash, _ = last_rest.split("|", 1)
    
    # Edit the range
    new_text = "def test():\n    return 0"
    result = await edit_tool.execute({
        "file_path": temp_file,
        "hash": f"{first_line_num}:{first_hash}-{last_line_num}:{last_hash}",
        "new_text": new_text,
    })
    
    assert not result.is_error, f"Edit failed: {result.output}"
    
    # Verify the edit
    result = await read_tool.execute({"file_path": temp_file})
    assert not result.is_error
    assert "def test()" in result.output
    assert "return 0" in result.output


@pytest.mark.asyncio
async def test_edit_tool_hash_mismatch(temp_file):
    """Test that EditTool fails if the hash doesn't match."""
    edit_tool = EditTool()
    
    # Attempt to edit with an invalid hash
    result = await edit_tool.execute({
        "file_path": temp_file,
        "hash": "1:xx",  # Invalid hash
        "new_text": "def test():",
    })
    
    assert result.is_error
    assert "Invalid hash format" in result.output
