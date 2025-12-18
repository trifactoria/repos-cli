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

import re
import shlex
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


def format_table(
    headers: list[str],
    rows: list[list[Any]],
    title: str = ""
) -> str:
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


def shell_quote(s: str) -> str:
    """Shell-escape string for safe substitution in shell commands.

    This ensures that special characters, quotes, and other shell
    metacharacters are properly escaped so they're treated as literals.

    Args:
        s: String to escape

    Returns:
        Shell-safe quoted string
    """
    return shlex.quote(s)


class LexerState(Enum):
    """States for quote-aware lexer."""
    NORMAL = auto()
    SINGLE_QUOTE = auto()
    DOUBLE_QUOTE = auto()
    ESCAPE = auto()


@dataclass
class ScriptSegment:
    """A segment of a script - either literal shell or an alias call."""
    type: str  # "literal" or "alias"
    content: str  # For literal: shell script; for alias: alias name
    args: list[str]  # For alias: parsed arguments; for literal: empty


def parse_alias_script(script: str) -> list[ScriptSegment]:
    """Parse alias script into segments, detecting @alias calls.

    This is a quote-aware parser that:
    - Only recognizes @alias when in NORMAL state (not inside quotes)
    - Only recognizes @alias at command boundaries
      (after ; or newline, or at start)
    - Properly handles single quotes, double quotes, and escape sequences
    - Never creates segments for delimiters (;, newline)
    - Supports @a @b shorthand for sequential alias calls

    Args:
        script: The alias script body

    Returns:
        List of ScriptSegment objects representing the parsed script
    """
    segments: list[ScriptSegment] = []

    if not script:
        return segments

    state = LexerState.NORMAL
    current_segment = []
    at_command_boundary = True  # Start of script is a command boundary

    i = 0
    while i < len(script):
        ch = script[i]

        # State machine for quote tracking
        if state == LexerState.ESCAPE:
            # After backslash in NORMAL or DOUBLE_QUOTE, consume one char
            current_segment.append(ch)
            state = LexerState.NORMAL
            i += 1
            at_command_boundary = False
            continue

        if state == LexerState.NORMAL:
            if ch == '\\':
                current_segment.append(ch)
                state = LexerState.ESCAPE
                i += 1
                continue
            elif ch == "'":
                current_segment.append(ch)
                state = LexerState.SINGLE_QUOTE
                i += 1
                at_command_boundary = False
                continue
            elif ch == '"':
                current_segment.append(ch)
                state = LexerState.DOUBLE_QUOTE
                i += 1
                at_command_boundary = False
                continue
            elif ch in (';', '\n'):
                # Command separator - flush current segment, skip delimiter
                if current_segment:
                    literal_text = ''.join(current_segment).strip()
                    if literal_text:
                        segments.append(
                            ScriptSegment(
                                type="literal",
                                content=literal_text,
                                args=[]
                            )
                        )
                    current_segment = []
                # Skip the delimiter - do NOT add it to any segment
                at_command_boundary = True
                i += 1
                continue
            elif ch in (' ', '\t'):
                # Whitespace - add to current segment if not at boundary
                if not at_command_boundary or current_segment:
                    current_segment.append(ch)
                i += 1
                continue
            elif ch == '@' and at_command_boundary:
                # Potential alias call - check if it's @name
                # Save current segment as literal if non-empty
                if current_segment:
                    literal_text = ''.join(current_segment).strip()
                    if literal_text:
                        segments.append(
                            ScriptSegment(
                                type="literal",
                                content=literal_text,
                                args=[]
                            )
                        )
                    current_segment = []

                # Parse @alias_name
                j = i + 1
                while (j < len(script) and
                       (script[j].isalnum() or script[j] == '_')):
                    j += 1

                alias_name = script[i+1:j]

                if not alias_name:
                    # Just @ by itself - treat as literal
                    current_segment.append(ch)
                    i += 1
                    at_command_boundary = False
                    continue

                # Skip whitespace after alias name
                while j < len(script) and script[j] in (' ', '\t'):
                    j += 1

                # Collect arguments, watch for @name patterns
                # Collect until separator (;, newline) or @name
                args_start = j
                at_token_boundary = True

                while j < len(script):
                    if script[j] in (';', '\n'):
                        # Hit a separator, stop collecting args
                        break
                    elif script[j] in (' ', '\t'):
                        # Whitespace marks token boundary
                        at_token_boundary = True
                        j += 1
                        continue
                    elif script[j] == '@' and at_token_boundary:
                        # @a @b shorthand case
                        # Stop collecting, handle @name next iteration
                        break
                    else:
                        # Regular character
                        at_token_boundary = False
                        j += 1

                # Parse collected arguments
                args_str = script[args_start:j].strip()
                if args_str:
                    try:
                        parsed_args = shlex.split(args_str)
                    except ValueError:
                        # Shlex parse error - treat whole as literal
                        current_segment.append(script[i:j])
                        i = j
                        boundary = script[j-1] in (';', '\n')
                        at_command_boundary = boundary if j > i else False
                        continue
                else:
                    parsed_args = []

                # Add alias segment
                segments.append(
                    ScriptSegment(
                        type="alias",
                        content=alias_name,
                        args=parsed_args
                    )
                )

                i = j
                # Check if we stopped at a delimiter or @name
                if i < len(script):
                    if script[i] in (';', '\n'):
                        at_command_boundary = True
                    elif script[i] == '@':
                        at_command_boundary = True
                    else:
                        at_command_boundary = False
                else:
                    at_command_boundary = False
                continue
            else:
                # Regular character in NORMAL state
                current_segment.append(ch)
                at_command_boundary = False
                i += 1
                continue

        elif state == LexerState.SINGLE_QUOTE:
            current_segment.append(ch)
            if ch == "'":
                state = LexerState.NORMAL
            i += 1
            continue

        elif state == LexerState.DOUBLE_QUOTE:
            if ch == '\\':
                current_segment.append(ch)
                # In double quotes, backslash escapes special chars
                if i + 1 < len(script):
                    i += 1
                    current_segment.append(script[i])
            elif ch == '"':
                current_segment.append(ch)
                state = LexerState.NORMAL
            else:
                current_segment.append(ch)
            i += 1
            continue

    # Flush remaining segment
    if current_segment:
        literal_text = ''.join(current_segment).strip()
        if literal_text:
            segments.append(
                ScriptSegment(
                    type="literal",
                    content=literal_text,
                    args=[]
                )
            )

    return segments


def extract_kwargs_and_posargs(
    args: list[str]
) -> tuple[dict[str, str], list[str]]:
    """Extract kwargs (key=value) and positional args from list.

    Args:
        args: List of argument tokens

    Returns:
        Tuple of (kwargs dict, positional args list)
    """
    kwargs: dict[str, str] = {}
    posargs: list[str] = []

    kwarg_pattern = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$')

    for arg in args:
        match = kwarg_pattern.match(arg)
        if match:
            key = match.group(1)
            value = match.group(2)
            kwargs[key] = value
        else:
            posargs.append(arg)

    return kwargs, posargs


def is_quote_balanced(text: str) -> bool:
    """Check if quotes are balanced in a shell command string.

    Handles:
    - Single quotes ('...')
    - Double quotes ("...")
    - Backslash escapes (both in normal and double-quote contexts)

    Args:
        text: Shell command text to check

    Returns:
        True if quotes are balanced, False otherwise
    """
    state = LexerState.NORMAL
    i = 0

    while i < len(text):
        ch = text[i]

        if state == LexerState.ESCAPE:
            # After backslash, consume one char and return to NORMAL
            state = LexerState.NORMAL
            i += 1
            continue

        if state == LexerState.NORMAL:
            if ch == '\\':
                state = LexerState.ESCAPE
            elif ch == "'":
                state = LexerState.SINGLE_QUOTE
            elif ch == '"':
                state = LexerState.DOUBLE_QUOTE
            i += 1

        elif state == LexerState.SINGLE_QUOTE:
            if ch == "'":
                state = LexerState.NORMAL
            i += 1

        elif state == LexerState.DOUBLE_QUOTE:
            if ch == '\\':
                # In double quotes, backslash escapes some chars
                if i + 1 < len(text):
                    i += 2  # Skip the escaped char
                    continue
                else:
                    i += 1  # Backslash at end
            elif ch == '"':
                state = LexerState.NORMAL
                i += 1
            else:
                i += 1

    # Balanced if we end in NORMAL state
    return state == LexerState.NORMAL


def has_trailing_backslash(text: str) -> bool:
    """Check if text ends with unescaped backslash (continuation).

    This checks if the text ends with a backslash in NORMAL or
    DOUBLE_QUOTE state that would cause shell continuation.

    Args:
        text: Shell command text to check

    Returns:
        True if text ends with unescaped backslash, False otherwise
    """
    if not text:
        return False

    state = LexerState.NORMAL
    i = 0

    while i < len(text):
        ch = text[i]

        if state == LexerState.ESCAPE:
            # After backslash, consume one char and return to NORMAL
            state = LexerState.NORMAL
            i += 1
            continue

        if state == LexerState.NORMAL:
            if ch == '\\':
                state = LexerState.ESCAPE
            elif ch == "'":
                state = LexerState.SINGLE_QUOTE
            elif ch == '"':
                state = LexerState.DOUBLE_QUOTE
            i += 1

        elif state == LexerState.SINGLE_QUOTE:
            if ch == "'":
                state = LexerState.NORMAL
            i += 1

        elif state == LexerState.DOUBLE_QUOTE:
            if ch == '\\':
                # In double quotes, backslash escapes some chars
                if i + 1 < len(text):
                    i += 2  # Skip the escaped char
                    continue
                else:
                    # Backslash at end in double quotes - continuation
                    return True
            elif ch == '"':
                state = LexerState.NORMAL
                i += 1
            else:
                i += 1

    # Check if we ended in ESCAPE state (backslash at end in NORMAL)
    return state == LexerState.ESCAPE


def is_shell_input_incomplete(text: str) -> bool:
    """Check if shell input is incomplete and needs continuation.

    Input is incomplete if:
    1. Quotes are unbalanced (open quote not closed)
    2. Text ends with unescaped backslash (line continuation)

    Args:
        text: Shell command text to check

    Returns:
        True if input needs continuation, False if complete
    """
    # Check for unbalanced quotes
    if not is_quote_balanced(text):
        return True

    # Check for trailing backslash continuation
    if has_trailing_backslash(text):
        return True

    return False


def substitute_placeholders(
    script: str,
    kwargs: dict[str, str]
) -> tuple[str, list[str]]:
    """Substitute {placeholder} with shell-safe values from kwargs.

    Raises an error if placeholders are missing.

    Args:
        script: Script with {placeholder} syntax
        kwargs: Dictionary of placeholder values

    Returns:
        Tuple of (substituted script, list of errors if any)

    Raises:
        ValueError: If required placeholders are missing
    """
    # Find all placeholders
    placeholder_pattern = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)\}')
    placeholders = placeholder_pattern.findall(script)

    if not placeholders:
        return script, []

    # Check for missing placeholders
    missing = [p for p in placeholders if p not in kwargs]
    if missing:
        provided_keys = list(kwargs.keys())
        prov = ', '.join(provided_keys) if provided_keys else 'none'
        error_msg = (
            f"Missing required placeholders: {', '.join(missing)}. "
            f"Provided: {prov}"
        )
        raise ValueError(error_msg)

    # Substitute all placeholders with shell-safe quoted values
    result = script
    for placeholder in set(placeholders):
        value = kwargs[placeholder]
        safe_value = shell_quote(value)
        result = result.replace(f'{{{placeholder}}}', safe_value)

    return result, []
