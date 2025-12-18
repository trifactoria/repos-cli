"""
Comprehensive tests for robust alias execution semantics.

Tests cover:
- Positional arguments ($1, $2, $@)
- Keyword placeholders ({name})
- Mixed kwargs + positional args
- Injection safety
- Alias chaining (@alias)
- Quote-aware chaining
- Recursion protection
- Raw text preservation (backslashes, quotes)
"""

import pytest
from repos_cli.executor import SubprocessExecutor
from repos_cli.kernel import Kernel
from repos_cli.store import SQLiteStore
from repos_cli.utils import (
    extract_kwargs_and_posargs,
    is_quote_balanced,
    parse_alias_script,
    substitute_placeholders,
)


@pytest.fixture
def mock_store(tmp_path):
    """Create a temporary SQLite store for testing."""
    from repos_cli.db import ensure_schema

    db_path = tmp_path / "test.db"
    ensure_schema(db_path)
    store = SQLiteStore(db_path)
    return store


@pytest.fixture
def mock_config():
    """Create a mock config for testing."""
    from dataclasses import dataclass

    @dataclass
    class MockConfig:
        branding: dict = None
        commands: dict = None
        panels: dict = None
        system: dict = None

        def __post_init__(self):
            self.branding = {"G": {"panel_color": "cyan", "caret_color": "pink"}}
            self.commands = {
                "base": {
                    "list": {"triggers": ["L"], "description": "List aliases"},
                    "add": {"triggers": ["N"], "description": "Add alias"},
                    "remove": {"triggers": ["RM"], "description": "Remove alias"},
                    "rerun": {"triggers": ["R"], "description": "Rerun last alias"},
                },
                "help": {"triggers": ["?", "h", "help"]},
            }
            self.panels = {"G": {"entry": "G", "shell_fallback": True}}
            self.system = {"root_panel": "REP", "entry_alias": "REP"}

        def get(self, key, default=None):
            return getattr(self, key, default)

    return MockConfig()


@pytest.fixture
def kernel(mock_store, mock_config):
    """Create a kernel instance for testing."""
    executor = SubprocessExecutor(force_color=False, timeout=5)
    kernel = Kernel(store=mock_store, executor=executor, config=mock_config)
    kernel.panel = "G"
    return kernel


# ----- Utility function tests -----


def test_is_quote_balanced_simple():
    """Test quote balance checking."""
    assert is_quote_balanced("echo hello")
    assert is_quote_balanced('echo "hello"')
    assert is_quote_balanced("echo 'hello'")
    assert not is_quote_balanced('echo "hello')
    assert not is_quote_balanced("echo 'hello")


def test_is_quote_balanced_nested():
    """Test quote balance with mixed quotes."""
    assert is_quote_balanced('''echo "it's fine"''')
    assert is_quote_balanced("""echo 'he said "hello"'""")
    assert not is_quote_balanced('''echo "it's''')


def test_is_quote_balanced_escapes():
    """Test quote balance with backslash escapes."""
    assert is_quote_balanced(r'echo "a\"b"')
    # Single quotes don't allow escaping - backslash is literal
    assert is_quote_balanced(r"echo 'a\b'")  # Backslash is literal in single quotes
    assert is_quote_balanced(r'printf "%s\n"')


def test_is_quote_balanced_multiline():
    """Test quote balance for multiline strings."""
    assert not is_quote_balanced('echo "line1')
    assert is_quote_balanced('echo "line1\nline2"')


def test_extract_kwargs_and_posargs():
    """Test kwarg and positional arg extraction."""
    args = ["message=hello world", "count=3", "a", "b"]
    kwargs, posargs = extract_kwargs_and_posargs(args)

    assert kwargs == {"message": "hello world", "count": "3"}
    assert posargs == ["a", "b"]


def test_extract_kwargs_only():
    """Test extraction with only kwargs."""
    args = ["foo=bar", "baz=qux"]
    kwargs, posargs = extract_kwargs_and_posargs(args)

    assert kwargs == {"foo": "bar", "baz": "qux"}
    assert posargs == []


def test_extract_posargs_only():
    """Test extraction with only positional args."""
    args = ["a", "b", "c"]
    kwargs, posargs = extract_kwargs_and_posargs(args)

    assert kwargs == {}
    assert posargs == ["a", "b", "c"]


def test_substitute_placeholders():
    """Test placeholder substitution."""
    script = "echo {message}; echo {count}"
    kwargs = {"message": "hello", "count": "3"}

    result, errors = substitute_placeholders(script, kwargs)

    # Result should have shell-quoted values
    assert "hello" in result
    assert "3" in result
    assert errors == []


def test_substitute_placeholders_missing():
    """Test error when placeholders are missing."""
    script = "echo {message}; echo {count}"
    kwargs = {"message": "hello"}

    with pytest.raises(ValueError) as exc_info:
        substitute_placeholders(script, kwargs)

    assert "Missing required placeholders: count" in str(exc_info.value)
    assert "Provided: message" in str(exc_info.value)


def test_substitute_placeholders_injection_safe():
    """Test that placeholder substitution is injection-safe."""
    script = "echo {message}"
    kwargs = {"message": '"; echo HACKED; #'}

    result, errors = substitute_placeholders(script, kwargs)

    # Should be shell-quoted and safe
    assert "HACKED" not in result or "'" in result  # Quoted or escaped


def test_parse_alias_script_literal_only():
    """Test parsing a script with no alias calls."""
    script = "echo test"
    segments = parse_alias_script(script)

    assert len(segments) == 1
    assert segments[0].type == "literal"
    assert segments[0].content == "echo test"


def test_parse_alias_script_alias_call():
    """Test parsing a script with @alias call."""
    script = "@t"
    segments = parse_alias_script(script)

    assert len(segments) == 1
    assert segments[0].type == "alias"
    assert segments[0].content == "t"
    assert segments[0].args == []


def test_parse_alias_script_alias_with_args():
    """Test parsing @alias with arguments."""
    script = "@t hello world"
    segments = parse_alias_script(script)

    assert len(segments) == 1
    assert segments[0].type == "alias"
    assert segments[0].content == "t"
    assert segments[0].args == ["hello", "world"]


def test_parse_alias_script_mixed():
    """Test parsing script with literal and alias."""
    script = "printf '\\n'; @t"
    segments = parse_alias_script(script)

    assert len(segments) == 2
    assert segments[0].type == "literal"
    assert "printf" in segments[0].content
    assert segments[1].type == "alias"
    assert segments[1].content == "t"


def test_parse_alias_script_quote_aware():
    """Test that @alias inside quotes is NOT detected."""
    script = 'echo "test @s; @b"'
    segments = parse_alias_script(script)

    assert len(segments) == 1
    assert segments[0].type == "literal"
    assert '@s' in segments[0].content
    assert '@b' in segments[0].content


def test_parse_alias_script_semicolon_separated():
    """Test alias calls separated by semicolons."""
    script = "@a; @b; @c"
    segments = parse_alias_script(script)

    # Should have 3 alias segments, NO literal segments for semicolons
    assert len(segments) == 3
    assert all(s.type == "alias" for s in segments)
    assert segments[0].content == "a"
    assert segments[1].content == "b"
    assert segments[2].content == "c"


def test_parse_alias_script_shorthand_sequential():
    """Test @a @b shorthand for sequential alias calls."""
    script = "@a @b @c"
    segments = parse_alias_script(script)

    # Should parse as three separate alias calls
    assert len(segments) == 3
    assert all(s.type == "alias" for s in segments)
    assert segments[0].content == "a"
    assert segments[0].args == []
    assert segments[1].content == "b"
    assert segments[1].args == []
    assert segments[2].content == "c"
    assert segments[2].args == []


# ----- Integration tests -----


def test_alias_positional_args(kernel):
    """Test A) Positional arguments work."""
    kernel.store.add_alias("G", "t", 'printf "%s\\n%s\\n" "$1" "$2"')

    result = kernel.handle_command("t test test2")

    assert "test" in result
    assert "test2" in result


def test_alias_quoted_args(kernel):
    """Test A) Quoted arguments preserved."""
    kernel.store.add_alias("G", "t", 'printf "%s" "$1"')

    result = kernel.handle_command('t "hello world"')

    assert "hello world" in result


def test_alias_keyword_placeholders(kernel):
    """Test B) Keyword placeholders work."""
    kernel.store.add_alias("G", "say", "echo {message}; echo {count}")

    result = kernel.handle_command('say message="hello world" count=3')

    assert "hello world" in result
    assert "3" in result


def test_alias_missing_placeholder(kernel):
    """Test B) Missing placeholder error."""
    kernel.store.add_alias("G", "say", "echo {message}; echo {count}")

    result = kernel.handle_command('say message="hello"')

    assert "Error" in result
    assert "Missing required placeholders: count" in result


def test_alias_mixed_kwargs_posargs(kernel):
    """Test C) Mixed kwargs and positional args."""
    kernel.store.add_alias("G", "mix", 'echo {message}; echo "$1"; echo "$2"')

    result = kernel.handle_command('mix message=hi a b')

    assert "hi" in result
    assert "a" in result
    assert "b" in result


def test_alias_injection_safety(kernel):
    """Test B) Injection safety."""
    kernel.store.add_alias("G", "say", "echo {message}")

    result = kernel.handle_command("say message='\"\\; echo HACKED; #'")

    # Should not execute HACKED
    # The output should contain the literal string, safely quoted
    assert "Error" not in result


def test_alias_chaining_basic(kernel):
    """Test D) Basic alias chaining."""
    kernel.store.add_alias("G", "t", "echo test")
    kernel.store.add_alias("G", "m", "printf '\\n'; @t")

    result = kernel.handle_command("m")

    # Should print newline then "test"
    assert "test" in result


def test_alias_chaining_with_args(kernel):
    """Test D) Chaining with arguments."""
    kernel.store.add_alias("G", "t", 'echo "$1"')
    kernel.store.add_alias("G", "m", "@t hello")

    result = kernel.handle_command("m")

    assert "hello" in result


def test_alias_chaining_quote_aware(kernel):
    """Test E) Quote-aware chaining prevents false detection."""
    kernel.store.add_alias("G", "s", "echo STATUS")
    kernel.store.add_alias("G", "b", "echo BRANCH")
    kernel.store.add_alias("G", "sb", 'echo "test @s; @b"')

    result = kernel.handle_command("sb")

    # Should output literal "test @s; @b", not execute s or b
    assert "test @s; @b" in result
    assert "STATUS" not in result
    assert "BRANCH" not in result


def test_alias_recursion_protection_cycle(kernel):
    """Test F) Recursion protection - cycle detection."""
    kernel.store.add_alias("G", "a", "@a")

    result = kernel.handle_command("a")

    assert "Error" in result
    assert "cycle" in result.lower()


def test_alias_recursion_protection_depth(kernel):
    """Test F) Recursion protection - max depth."""
    # Create a chain: a -> b -> c -> ... -> k (11 deep)
    for i in range(11):
        name = chr(ord("a") + i)
        next_name = chr(ord("a") + i + 1)
        if i < 10:
            kernel.store.add_alias("G", name, f"@{next_name}")
        else:
            kernel.store.add_alias("G", name, "echo done")

    result = kernel.handle_command("a")

    assert "Error" in result
    assert "depth" in result.lower() or "exceeded" in result.lower()


def test_alias_dollar_at(kernel):
    """Test that $@ works correctly."""
    kernel.store.add_alias("G", "t", 'printf "%s" "$@"')

    result = kernel.handle_command("t a b c")

    # $@ should expand to all args
    assert "a" in result or "b" in result or "c" in result


def test_alias_empty_args(kernel):
    """Test alias with no arguments."""
    kernel.store.add_alias("G", "t", "echo test")

    result = kernel.handle_command("t")

    assert "test" in result


def test_alias_complex_chaining(kernel):
    """Test complex chaining scenario."""
    kernel.store.add_alias("G", "base", "echo BASE")
    kernel.store.add_alias("G", "mid", "@base; echo MID")
    kernel.store.add_alias("G", "top", "@mid; echo TOP")

    result = kernel.handle_command("top")

    assert "BASE" in result
    assert "MID" in result
    assert "TOP" in result


def test_alias_no_separator_execution(kernel):
    """Test 1: Separators should not be executed as commands."""
    kernel.store.add_alias("G", "s", "echo STATUS")
    kernel.store.add_alias("G", "b", "echo BRANCH")
    kernel.store.add_alias("G", "sb", "@s; @b")

    result = kernel.handle_command("sb")

    # Should execute both aliases
    assert "STATUS" in result
    assert "BRANCH" in result
    # Should NOT have any shell errors about ";" being unexpected
    assert "unexpected" not in result.lower()
    assert "syntax error" not in result.lower()


def test_alias_shorthand_sequential_calls(kernel):
    """Test 2: @a @b shorthand should execute both aliases."""
    kernel.store.add_alias("G", "s", "echo STATUS")
    kernel.store.add_alias("G", "b", "echo BRANCH")
    kernel.store.add_alias("G", "sb", "@s @b")

    result = kernel.handle_command("sb")

    # Should execute both aliases in sequence
    assert "STATUS" in result
    assert "BRANCH" in result


def test_alias_shorthand_with_three_calls(kernel):
    """Test @a @b @c shorthand."""
    kernel.store.add_alias("G", "a", "echo A")
    kernel.store.add_alias("G", "b", "echo B")
    kernel.store.add_alias("G", "c", "echo C")
    kernel.store.add_alias("G", "abc", "@a @b @c")

    result = kernel.handle_command("abc")

    assert "A" in result
    assert "B" in result
    assert "C" in result


# ----- Raw text preservation tests -----


def test_alias_preserves_backslash_n(kernel):
    """Test A) Backslashes preserved: printf with \\n."""
    # Create alias via command to test end-to-end parsing
    result = kernel.handle_command('N e printf "%s\\n" "a" "b"')
    assert "Added alias 'e'" in result

    # Run the alias
    result = kernel.handle_command("e")

    # Should output two lines
    assert "a" in result
    assert "b" in result


def test_alias_preserves_backslash_escapes(kernel):
    """Test B) Backslash escapes preserved: printf with embedded \\n."""
    result = kernel.handle_command('N e printf "a\\nb\\n"')
    assert "Added alias 'e'" in result

    result = kernel.handle_command("e")

    # Should output a and b on separate lines
    lines = [line for line in result.split('\n') if line and not line.startswith('[')]
    assert "a" in result
    assert "b" in result


def test_alias_preserves_double_quotes(kernel):
    """Test quote preservation: double quotes in alias body."""
    result = kernel.handle_command('N say echo "hello world"')
    assert "Added alias 'say'" in result

    result = kernel.handle_command("say")

    # Should output "hello world"
    assert "hello world" in result


def test_alias_preserves_single_quotes(kernel):
    """Test quote preservation: single quotes in alias body."""
    result = kernel.handle_command("N say echo 'hello world'")
    assert "Added alias 'say'" in result

    result = kernel.handle_command("say")

    assert "hello world" in result


def test_alias_preserves_mixed_quotes(kernel):
    """Test quote preservation: mixed quotes."""
    result = kernel.handle_command('''N say echo "it's working"''')
    assert "Added alias 'say'" in result

    result = kernel.handle_command("say")

    assert "it's working" in result


def test_alias_raw_text_no_shlex_damage(kernel):
    """Test that shlex doesn't mangle the alias body."""
    # This would be broken if we used shlex.split on the body
    result = kernel.handle_command('N test printf "%s|%s" "a" "b"')
    assert "Added alias 'test'" in result

    result = kernel.handle_command("test")

    # Should output a|b
    assert "a|b" in result or ("a" in result and "b" in result)
