# RepOS™ — Multi-Panel REPL-Based Developer Command Environment
# Copyright (c) 2025
# TriFactoria (Andrew Blankfield)
#
# Licensed under the Business Source License 1.1 (BSL 1.1).
# You may use, modify, and redistribute this file under the terms of the BSL.
# On the Change Date (2029-01-01), this file will be licensed under
# the Apache License, Version 2.0.

"""
Utility functions for RepOS.
"""

from typing import Any


def format_table(headers: list[str], rows: list[list[Any]], title: str = "") -> str:
    """
    Format data as a simple text table without external dependencies.

    Args:
        headers: List of column header names
        rows: List of rows, where each row is a list of values
        title: Optional title to display above the table

    Returns:
        Formatted table as a string
    """
    if not rows:
        return ""

    # Convert all values to strings
    str_headers = [str(h) for h in headers]
    str_rows = [[str(val) for val in row] for row in rows]

    # Calculate column widths (max of header and all row values)
    col_widths = []
    for i, header in enumerate(str_headers):
        max_width = len(header)
        for row in str_rows:
            if i < len(row):
                max_width = max(max_width, len(row[i]))
        col_widths.append(max_width)

    # Build the table
    lines = []

    # Add title if provided
    if title:
        lines.append(title)

    # Add header row
    header_parts = []
    for i, header in enumerate(str_headers):
        header_parts.append(header.ljust(col_widths[i]))
    lines.append("  ".join(header_parts))

    # Add data rows
    for row in str_rows:
        row_parts = []
        for i, val in enumerate(row):
            row_parts.append(val.ljust(col_widths[i]))
        lines.append("  ".join(row_parts))

    return "\n".join(lines)
