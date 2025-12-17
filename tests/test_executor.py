"""
Tests for Subprocess implementation of Executor Protocol.
Covers command execution in isolation from Kernel.
"""

from __future__ import annotations

import subprocess

import pytest

from repos_cli.executor import SubprocessExecutor


@pytest.fixture
def executor() -> SubprocessExecutor:
    """Create executor with default settings."""
    return SubprocessExecutor(force_color=True)


# ----------------------------------------------------------------
# Basic execution
# ----------------------------------------------------------------


def test_executor_runs_simple_command():
    """Executor must run command and return output."""
    executor = SubprocessExecutor()

    exit_code, stdout, stderr, started_at, duration_ms = executor.run("echo test")

    assert exit_code == 0
    assert "test" in stdout
    assert stderr == ""
    assert started_at is not None
    assert duration_ms >= 0


def test_executor_captures_stderr():
    """Executor must capture stderr separately from stdout."""
    executor = SubprocessExecutor()

    # Use a command that writes to stderr (cross-platform)
    exit_code, stdout, stderr, _, _ = executor.run(
        "python -c 'import sys; sys.stderr.write(\"error\")'"
    )

    assert "error" in stderr
    assert stdout == ""


def test_executor_returns_non_zero_exit_code():
    """Executor must return actual exit code from failed commands."""
    executor = SubprocessExecutor()

    exit_code, _, _, _, _ = executor.run("python -c 'import sys; sys.exit(42)'")

    assert exit_code == 42


# ----------------------------------------------------------------
# Environment variables (force color)
# ----------------------------------------------------------------


def test_executor_sets_color_env_when_force_color_true():
    """Executor with force_color=True must set color environment variables."""
    executor = SubprocessExecutor(force_color=True)

    # Run command that echoes an env var
    _, stdout, _, _, _ = executor.run("python -c 'import os; print(os.getenv(\"FORCE_COLOR\"))'")

    assert "1" in stdout


def test_executor_does_not_set_color_env_when_force_color_false():
    """Executor with force_color=False must not set color variables."""
    executor = SubprocessExecutor(force_color=False)

    _, stdout, _, _, _ = executor.run(
        'python -c \'import os; print(os.getenv("FORCE_COLOR", "NONE"))\''
    )

    assert "NONE" in stdout


# ----------------------------------------------------------------
# Timeout handling
# ----------------------------------------------------------------


def test_executor_handles_timeout():
    """Executor must catch TimeoutExpired and return error."""
    executor = SubprocessExecutor(timeout=1)

    # Command that sleeps longer than timeout
    exit_code, stdout, stderr, _, _ = executor.run("python -c 'import time; time.sleep(10)'")

    assert exit_code == 1
    assert stdout == ""
    assert "timed out" in stderr.lower()


# ----------------------------------------------------------------
# Exception handling
# ----------------------------------------------------------------


def test_executor_handles_command_not_found():
    """Executor must handle non-existent commands gracefully."""
    executor = SubprocessExecutor()

    exit_code, stdout, stderr, _, _ = executor.run("this_command_definitely_does_not_exist_12345")

    assert exit_code == 1
    assert stdout == ""
    assert "Error executing command" in stderr or "not found" in stderr.lower()


def test_executor_handles_runtime_exception(monkeypatch: pytest.MonkeyPatch):
    """Executor must handle subprocess.run exceptions."""
    executor = SubprocessExecutor()

    def fake_run(*args, **kwargs):
        raise RuntimeError("test exception")

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code, stdout, stderr, _, _ = executor.run("any command")

    assert exit_code == 1
    assert stdout == ""
    assert "Error executing command" in stderr
    assert "test exception" in stderr


# ----------------------------------------------------------------
# Streaming execution (run_stream)
# ----------------------------------------------------------------


def test_executor_run_stream_basic():
    """run_stream must execute command and call callbacks."""
    executor = SubprocessExecutor()

    stdout_lines = []
    stderr_lines = []

    def on_stdout(line: str):
        stdout_lines.append(line)

    def on_stderr(line: str):
        stderr_lines.append(line)

    result = executor.run_stream("echo test", on_stdout=on_stdout, on_stderr=on_stderr)

    assert result.exit_code == 0
    assert "test" in result.stdout
    assert len(stdout_lines) > 0
    assert "test" in "".join(stdout_lines)
    assert result.started_at is not None
    assert result.duration_ms >= 0


def test_executor_run_stream_captures_stderr():
    """run_stream must capture and stream stderr."""
    executor = SubprocessExecutor()

    stderr_lines = []

    def on_stderr(line: str):
        stderr_lines.append(line)

    result = executor.run_stream(
        "python -c 'import sys; sys.stderr.write(\"error\\n\")'", on_stderr=on_stderr
    )

    assert "error" in result.stderr
    assert len(stderr_lines) > 0
    assert "error" in "".join(stderr_lines)


def test_executor_run_stream_timeout():
    """run_stream must handle timeout and terminate process."""
    executor = SubprocessExecutor()

    result = executor.run_stream("python -c 'import time; time.sleep(10)'", timeout=1)

    assert result.exit_code == 1
    assert "timed out" in result.stderr.lower()


def test_executor_run_stream_max_capture_bytes():
    """run_stream must truncate output when exceeding max_capture_bytes."""
    executor = SubprocessExecutor(max_capture_bytes=100)

    # Generate more than 100 bytes of output
    result = executor.run_stream("python -c 'print(\"x\" * 1000)'")

    assert result.truncated is True
    assert result.stdout_bytes > 100


def test_executor_run_stream_no_callbacks():
    """run_stream must work without callbacks."""
    executor = SubprocessExecutor()

    result = executor.run_stream("echo test")

    assert result.exit_code == 0
    assert "test" in result.stdout


def test_executor_run_stream_handles_popen_exception(monkeypatch: pytest.MonkeyPatch):
    """run_stream must handle Popen exceptions."""
    executor = SubprocessExecutor()

    def fake_popen(*args, **kwargs):
        raise RuntimeError("popen failed")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = executor.run_stream("any command")

    assert result.exit_code == 1
    assert "Error executing command" in result.stderr
    assert "popen failed" in result.stderr


def test_executor_run_stream_with_cwd(tmp_path):
    """run_stream must respect cwd parameter."""
    executor = SubprocessExecutor()

    test_file = tmp_path / "test.txt"
    test_file.write_text("hello")

    result = executor.run_stream("cat test.txt", cwd=str(tmp_path))

    assert result.exit_code == 0
    assert "hello" in result.stdout


def test_executor_run_stream_exit_code_127():
    """run_stream must normalize exit code 127 to 1."""
    executor = SubprocessExecutor()

    result = executor.run_stream("this_command_does_not_exist_xyz123")

    # Exit code 127 (command not found) should be normalized to 1
    assert result.exit_code == 1


# ----------------------------------------------------------------
# PTY execution (run_pty)
# ----------------------------------------------------------------


def test_executor_run_pty_basic():
    """run_pty must execute command with PTY and capture output."""
    executor = SubprocessExecutor()

    output_chunks = []

    def on_output(chunk: str):
        output_chunks.append(chunk)

    result = executor.run_pty("echo pty_test", on_output=on_output)

    # PTY output capture and callback work
    assert "pty_test" in result.stdout
    assert len(output_chunks) > 0
    # Verify result structure
    assert result.started_at is not None
    assert result.duration_ms >= 0
    assert result.stdout_bytes > 0


def test_executor_run_pty_timeout():
    """run_pty must handle timeout."""
    executor = SubprocessExecutor()

    result = executor.run_pty("python -c 'import time; time.sleep(10)'", timeout=1)

    assert result.exit_code == 1
    assert "timed out" in result.stdout.lower()


def test_executor_run_pty_max_capture_bytes():
    """run_pty must truncate output when exceeding max_capture_bytes."""
    executor = SubprocessExecutor(max_capture_bytes=50)

    result = executor.run_pty("python -c 'print(\"x\" * 1000)'")

    assert result.truncated is True
    assert result.stdout_bytes > 50


def test_executor_run_pty_with_cwd(tmp_path):
    """run_pty must respect cwd parameter."""
    executor = SubprocessExecutor()

    test_file = tmp_path / "pty_test.txt"
    test_file.write_text("pty_hello")

    result = executor.run_pty("cat pty_test.txt", cwd=str(tmp_path))

    # Verify command executed in correct directory
    assert "pty_hello" in result.stdout
    assert result.stdout_bytes > 0


def test_executor_run_pty_exit_code_127():
    """run_pty must normalize exit code 127 to 1."""
    executor = SubprocessExecutor()

    result = executor.run_pty("nonexistent_command_xyz999")

    assert result.exit_code == 1


def test_executor_run_pty_handles_popen_exception(monkeypatch: pytest.MonkeyPatch):
    """run_pty must handle Popen exceptions gracefully."""
    executor = SubprocessExecutor()

    original_popen = subprocess.Popen

    def fake_popen(*args, **kwargs):
        # Only fake the PTY Popen call (has preexec_fn)
        if "preexec_fn" in kwargs:
            raise RuntimeError("pty popen failed")
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = executor.run_pty("echo test")

    assert result.exit_code == 1
    assert "Error executing command" in result.stderr
    assert "pty popen failed" in result.stderr


# ----------------------------------------------------------------
# TTY execution (run_tty)
# ----------------------------------------------------------------


def test_executor_run_tty_basic():
    """run_tty must execute command with terminal control."""
    executor = SubprocessExecutor()

    result = executor.run_tty("echo tty_test")

    assert result.exit_code == 0
    assert result.started_at is not None
    assert result.duration_ms >= 0


def test_executor_run_tty_timeout():
    """run_tty must handle timeout."""
    executor = SubprocessExecutor()

    result = executor.run_tty("python -c 'import time; time.sleep(10)'", timeout=1)

    assert result.exit_code == 1


def test_executor_run_tty_with_cwd(tmp_path):
    """run_tty must respect cwd parameter."""
    executor = SubprocessExecutor()

    test_file = tmp_path / "tty_test.txt"
    test_file.write_text("tty_hello")

    result = executor.run_tty("cat tty_test.txt > /dev/null", cwd=str(tmp_path))

    assert result.exit_code == 0


def test_executor_run_tty_exit_code_127():
    """run_tty must normalize exit code 127 to 1."""
    executor = SubprocessExecutor()

    result = executor.run_tty("another_nonexistent_cmd_abc")

    assert result.exit_code == 1


def test_executor_run_tty_handles_exception(monkeypatch: pytest.MonkeyPatch):
    """run_tty must handle Popen exceptions."""
    executor = SubprocessExecutor()

    def fake_popen(*args, **kwargs):
        raise RuntimeError("tty popen failed")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = executor.run_tty("any command")

    assert result.exit_code == 1
