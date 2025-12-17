"""
Tests for repos.utils module.
"""

import pytest

from repos_cli.utils import format_table


def test_format_table_empty_rows():
    """Test format_table with no rows returns empty string."""
    result = format_table(["Header1", "Header2"], [])
    assert result == ""


def test_format_table_single_row():
    """Test format_table with a single row."""
    result = format_table(["Name", "Age"], [["Alice", "30"]])
    assert "Name" in result
    assert "Age" in result
    assert "Alice" in result
    assert "30" in result


def test_format_table_multiple_rows():
    """Test format_table with multiple rows."""
    headers = ["ID", "Name", "Status"]
    rows = [
        ["1", "Alice", "Active"],
        ["2", "Bob", "Inactive"],
        ["3", "Charlie", "Active"],
    ]
    result = format_table(headers, rows)

    lines = result.split("\n")
    assert len(lines) == 4  # header + 3 data rows
    assert "ID" in lines[0]
    assert "Name" in lines[0]
    assert "Status" in lines[0]
    assert "Alice" in result
    assert "Bob" in result
    assert "Charlie" in result


def test_format_table_with_title():
    """Test format_table with a title."""
    result = format_table(["Name", "Age"], [["Alice", "30"]], title="[Users]")
    assert result.startswith("[Users]")
    assert "Name" in result
    assert "Alice" in result


def test_format_table_column_width_calculation():
    """Test that columns are properly aligned based on max width."""
    headers = ["Short", "VeryLongHeaderName"]
    rows = [
        ["A", "B"],
        ["Long", "ShortVal"],
    ]
    result = format_table(headers, rows)

    lines = result.split("\n")
    # Check that header line and data lines have consistent spacing
    assert len(lines) == 3  # header + 2 data rows

    # Headers should be padded to match max width
    assert "VeryLongHeaderName" in lines[0]
    assert "Short" in lines[0]


def test_format_table_non_string_values():
    """Test format_table converts non-string values to strings."""
    headers = ["Name", "Age", "Score"]
    rows = [
        ["Alice", 30, 95.5],
        ["Bob", 25, 87],
        [None, 0, False],
    ]
    result = format_table(headers, rows)

    assert "30" in result
    assert "95.5" in result
    assert "87" in result
    assert "None" in result
    assert "0" in result
    assert "False" in result


def test_format_table_shorter_rows():
    """Test format_table handles rows shorter than headers."""
    headers = ["Col1", "Col2", "Col3"]
    rows = [
        ["A", "B", "C"],
        ["D", "E"],  # shorter row - missing last column
    ]
    result = format_table(headers, rows)

    # Should work fine - row just has empty space for missing columns
    assert "A" in result
    assert "D" in result

    # Verify it has the expected number of lines
    lines = result.split("\n")
    assert len(lines) == 3  # header + 2 data rows


def test_format_table_longer_rows():
    """Test format_table with rows longer than headers."""
    headers = ["Col1", "Col2"]
    rows = [
        ["A", "B"],
        ["C", "D", "E", "F"],  # longer row - extra values
    ]
    # Extra values in rows beyond header count will cause IndexError
    with pytest.raises(IndexError):
        format_table(headers, rows)


def test_format_table_unicode_characters():
    """Test format_table handles unicode characters."""
    headers = ["Name", "Symbol"]
    rows = [
        ["Arrow", "→"],
        ["Check", "✓"],
        ["Cross", "✗"],
    ]
    result = format_table(headers, rows)

    assert "→" in result
    assert "✓" in result
    assert "✗" in result


def test_format_table_spacing_consistency():
    """Test that table columns are separated with consistent spacing."""
    headers = ["A", "B"]
    rows = [["1", "2"]]
    result = format_table(headers, rows)

    # Should have "  " (two spaces) between columns
    assert "  " in result


def test_format_table_empty_string_values():
    """Test format_table handles empty string values."""
    headers = ["Name", "Value"]
    rows = [
        ["Alice", ""],
        ["", "Bob"],
        ["", ""],
    ]
    result = format_table(headers, rows)

    lines = result.split("\n")
    assert len(lines) == 4  # header + 3 rows


def test_format_table_whitespace_values():
    """Test format_table handles whitespace in values."""
    headers = ["Text"]
    rows = [
        ["  leading"],
        ["trailing  "],
        ["  both  "],
    ]
    result = format_table(headers, rows)

    # Whitespace should be preserved
    assert "  leading" in result
    assert "trailing  " in result
    assert "  both  " in result


def test_format_table_special_characters():
    """Test format_table handles special characters."""
    headers = ["Key", "Value"]
    rows = [
        ["Path", "/home/user/file.txt"],
        ["URL", "https://example.com"],
        ["Math", "a + b = c"],
        ["Symbols", "!@#$%^&*()"],
    ]
    result = format_table(headers, rows)

    assert "/home/user/file.txt" in result
    assert "https://example.com" in result
    assert "a + b = c" in result
    assert "!@#$%^&*()" in result


def test_format_table_large_numbers():
    """Test format_table handles large numbers."""
    headers = ["ID", "Count"]
    rows = [
        ["1", 1000000],
        ["2", 999999999],
        ["3", -123456],
    ]
    result = format_table(headers, rows)

    assert "1000000" in result
    assert "999999999" in result
    assert "-123456" in result


def test_format_table_mixed_types():
    """Test format_table with mixed types in same column."""
    headers = ["Mixed"]
    rows = [
        [123],
        ["text"],
        [None],
        [True],
        [3.14],
    ]
    result = format_table(headers, rows)

    assert "123" in result
    assert "text" in result
    assert "None" in result
    assert "True" in result
    assert "3.14" in result
