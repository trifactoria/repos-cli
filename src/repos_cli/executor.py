# RepOS™ — Multi-Panel REPL-Based Developer Command Environment
# Copyright (c) 2025
# TriFactoria (Andrew Blankfield)
#
# Licensed under the Business Source License 1.1 (BSL 1.1).
# You may use, modify, and redistribute this file under the terms of the BSL.
# On the Change Date (2029-01-01), this file will be licensed under
# the Apache License, Version 2.0.

"""
Subprocess-backed executor implementation for RepOS.

This module provides:
- run(): buffered execution (legacy / current kernel path)
- run_stream(): streaming stdout/stderr in real time
  (for prompt_toolkit UI)
- run_pty(): optional PTY execution for truly interactive commands
  (curses, prompts, etc.)

Kernel can continue using run() today. Later we patch kernel to
use run_stream() (and use run_pty() for shell passthrough like
'!<cmd>').
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StreamResult:
    exit_code: int
    stdout: str
    stderr: str
    started_at: str
    duration_ms: int
    stdout_bytes: int
    stderr_bytes: int
    truncated: bool


@dataclass(frozen=True)
class TTYResult:
    """Result from TTY/passthrough execution (no output capture)."""

    exit_code: int
    started_at: str
    duration_ms: int


class SubprocessExecutor:
    """Subprocess implementation of Executor protocol."""

    def __init__(
        self, force_color: bool = True, timeout: int = 30,
        max_capture_bytes: int = 256_000
    ):
        """Initialize executor with configuration.

        Args:
            force_color: If True, set color-forcing env variables
            timeout: Command timeout in seconds (default: 30)
            max_capture_bytes: Max bytes to keep in captured
                stdout/stderr buffers
        """
        self.force_color = force_color
        self.timeout = timeout
        self.max_capture_bytes = max_capture_bytes

    def _build_env(self) -> dict:
        env = os.environ.copy()
        if self.force_color:
            env["PY_COLORS"] = "1"
            env["FORCE_COLOR"] = "1"
            env["CLICOLOR_FORCE"] = "1"
        return env

    def run(
        self, command: str, cwd: str | None = None
    ) -> tuple[int, str, str, str, int]:
        """Run a shell command safely and return buffered results.

        Args:
            command: shell command to execute
            cwd: working directory for the command (default: current directory)

        Returns:
            (exit_code, stdout, stderr, started_at, duration_ms)
        """
        env = self._build_env()

        started_at = datetime.now().isoformat()
        start_time = datetime.now()

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
                cwd=cwd,
            )
            duration_ms = int(
                (datetime.now() - start_time).total_seconds() * 1000
            )
            exit_code = (
                1 if result.returncode == 127 else result.returncode
            )
            return (
                exit_code, result.stdout, result.stderr,
                started_at, duration_ms
            )
        except subprocess.TimeoutExpired:
            duration_ms = int(
                (datetime.now() - start_time).total_seconds() * 1000
            )
            return (
                1, "",
                f"Command timed out after {self.timeout} seconds",
                started_at, duration_ms
            )
        except Exception as e:
            duration_ms = int(
                (datetime.now() - start_time).total_seconds() * 1000
            )
            return (
                1, "", f"Error executing command: {e}",
                started_at, duration_ms
            )

    def run_argv(
        self, script: str, posargs: list[str] | None = None,
        cwd: str | None = None
    ) -> tuple[int, str, str, str, int]:
        """Run a script with positional arguments using sh -c.

        This provides proper support for $1, $2, $@, etc. by using:
        ["/bin/sh", "-c", script, "_", arg1, arg2, ...]

        Args:
            script: Shell script to execute
            posargs: Positional arguments (become $1, $2, etc.)
            cwd: Working directory for the command

        Returns:
            (exit_code, stdout, stderr, started_at, duration_ms)
        """
        env = self._build_env()
        started_at = datetime.now().isoformat()
        start_time = datetime.now()

        # Build argv: ["/bin/sh", "-c", script, "_", posargs...]
        argv = ["/bin/sh", "-c", script, "_"]
        if posargs:
            argv.extend(posargs)

        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
                cwd=cwd,
            )
            duration_ms = int(
                (datetime.now() - start_time).total_seconds() * 1000
            )
            exit_code = (
                1 if result.returncode == 127 else result.returncode
            )
            return (
                exit_code, result.stdout, result.stderr,
                started_at, duration_ms
            )
        except subprocess.TimeoutExpired:
            duration_ms = int(
                (datetime.now() - start_time).total_seconds() * 1000
            )
            return (
                1, "",
                f"Command timed out after {self.timeout} seconds",
                started_at, duration_ms
            )
        except Exception as e:
            duration_ms = int(
                (datetime.now() - start_time).total_seconds() * 1000
            )
            return (
                1, "", f"Error executing command: {e}",
                started_at, duration_ms
            )

    def run_argv_stream(
        self,
        script: str,
        posargs: list[str] | None = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        timeout: int | None = None,
        cwd: str | None = None,
    ) -> StreamResult:
        """Run a script with positional arguments and stream output.

        Args:
            script: Shell script to execute
            posargs: Positional arguments (become $1, $2, etc.)
            on_stdout: callback for stdout lines
            on_stderr: callback for stderr lines
            timeout: overrides self.timeout
            cwd: working directory

        Returns:
            StreamResult
        """
        env = self._build_env()
        started_at = datetime.now().isoformat()
        start_ts = time.time()

        cap_out: list[str] = []
        cap_err: list[str] = []
        out_bytes = 0
        err_bytes = 0
        truncated = False

        max_bytes = max(0, int(self.max_capture_bytes))
        deadline = start_ts + (
            timeout if timeout is not None else self.timeout
        )

        def _append_capped(buf: list[str], s: str, current_bytes: int) -> int:
            nonlocal truncated
            b = len(s.encode("utf-8", errors="replace"))
            if max_bytes == 0:
                truncated = True
                return current_bytes + b
            if current_bytes >= max_bytes:
                truncated = True
                return current_bytes + b
            remaining = max_bytes - current_bytes
            if b > remaining:
                raw = s.encode("utf-8", errors="replace")[:remaining]
                buf.append(raw.decode("utf-8", errors="replace"))
                truncated = True
                return current_bytes + b
            buf.append(s)
            return current_bytes + b

        # Build argv: ["/bin/sh", "-c", script, "_", posargs...]
        argv = ["/bin/sh", "-c", script, "_"]
        if posargs:
            argv.extend(posargs)

        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                cwd=cwd,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_ts) * 1000)
            return StreamResult(
                exit_code=1,
                stdout="",
                stderr=f"Error executing command: {e}",
                started_at=started_at,
                duration_ms=duration_ms,
                stdout_bytes=0,
                stderr_bytes=0,
                truncated=False,
            )

        assert proc.stdout is not None
        assert proc.stderr is not None

        stop_event = threading.Event()

        def _reader(pipe, is_err: bool) -> None:
            nonlocal out_bytes, err_bytes
            try:
                for line in iter(pipe.readline, ""):
                    if stop_event.is_set():
                        break
                    if is_err:
                        if on_stderr:
                            on_stderr(line)
                        err_bytes = _append_capped(cap_err, line, err_bytes)
                    else:
                        if on_stdout:
                            on_stdout(line)
                        out_bytes = _append_capped(cap_out, line, out_bytes)
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        t_out = threading.Thread(
            target=_reader, args=(proc.stdout, False), daemon=True
        )
        t_err = threading.Thread(
            target=_reader, args=(proc.stderr, True), daemon=True
        )
        t_out.start()
        t_err.start()

        timed_out = False
        try:
            while True:
                rc = proc.poll()
                if rc is not None:
                    break
                if time.time() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.03)
        finally:
            if timed_out:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

            stop_event.set()
            t_out.join(timeout=0.5)
            t_err.join(timeout=0.5)

        duration_ms = int((time.time() - start_ts) * 1000)

        if timed_out:
            timeout_val = (
                timeout if timeout is not None else self.timeout
            )
            msg = f"Command timed out after {timeout_val} seconds\n"
            if on_stderr:
                on_stderr(msg)
            err_bytes = _append_capped(cap_err, msg, err_bytes)
            exit_code = 1
        else:
            exit_code = proc.returncode if proc.returncode is not None else 1

        if exit_code == 127:
            exit_code = 1

        return StreamResult(
            exit_code=exit_code,
            stdout="".join(cap_out),
            stderr="".join(cap_err),
            started_at=started_at,
            duration_ms=duration_ms,
            stdout_bytes=out_bytes,
            stderr_bytes=err_bytes,
            truncated=truncated,
        )

    def run_stream(
        self,
        command: str,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        timeout: int | None = None,
        cwd: str | None = None,
    ) -> StreamResult:
        """Run a command and stream output line-by-line in real time.

        Notes:
        - Uses pipes (stdout/stderr). Great for most commands.
        - Some interactive/curses programs need a PTY: use run_pty().

        Args:
            command: shell command
            on_stdout: called with each stdout line (including newline
                if present)
            on_stderr: called with each stderr line
            timeout: overrides self.timeout
            cwd: working directory for the command (default: current directory)

        Returns:
            StreamResult (includes captured output up to max_capture_bytes)
        """
        env = self._build_env()
        started_at = datetime.now().isoformat()
        start_ts = time.time()

        cap_out: list[str] = []
        cap_err: list[str] = []
        out_bytes = 0
        err_bytes = 0
        truncated = False

        max_bytes = max(0, int(self.max_capture_bytes))
        deadline = start_ts + (
            timeout if timeout is not None else self.timeout
        )

        def _append_capped(buf: list[str], s: str, current_bytes: int) -> int:
            nonlocal truncated
            b = len(s.encode("utf-8", errors="replace"))
            if max_bytes == 0:
                truncated = True
                return current_bytes + b
            if current_bytes >= max_bytes:
                truncated = True
                return current_bytes + b
            # If this chunk would exceed the cap, partially keep it.
            remaining = max_bytes - current_bytes
            if b > remaining:
                # Keep a safe slice by bytes (best-effort).
                # Encode then slice and decode.
                raw = s.encode("utf-8", errors="replace")[:remaining]
                buf.append(raw.decode("utf-8", errors="replace"))
                truncated = True
                return current_bytes + b
            buf.append(s)
            return current_bytes + b

        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # line-buffered (best effort)
                env=env,
                cwd=cwd,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_ts) * 1000)
            return StreamResult(
                exit_code=1,
                stdout="",
                stderr=f"Error executing command: {e}",
                started_at=started_at,
                duration_ms=duration_ms,
                stdout_bytes=0,
                stderr_bytes=0,
                truncated=False,
            )

        assert proc.stdout is not None
        assert proc.stderr is not None

        stop_event = threading.Event()

        def _reader(pipe, is_err: bool) -> None:
            nonlocal out_bytes, err_bytes
            try:
                for line in iter(pipe.readline, ""):
                    if stop_event.is_set():
                        break
                    if is_err:
                        if on_stderr:
                            on_stderr(line)
                        err_bytes = _append_capped(cap_err, line, err_bytes)
                    else:
                        if on_stdout:
                            on_stdout(line)
                        out_bytes = _append_capped(cap_out, line, out_bytes)
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        t_out = threading.Thread(
            target=_reader, args=(proc.stdout, False), daemon=True
        )
        t_err = threading.Thread(
            target=_reader, args=(proc.stderr, True), daemon=True
        )
        t_out.start()
        t_err.start()

        timed_out = False
        try:
            while True:
                rc = proc.poll()
                if rc is not None:
                    break
                if time.time() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.03)
        finally:
            if timed_out:
                # Terminate nicely then kill if needed.
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

            stop_event.set()
            # Join threads quickly; they should drain/stop.
            t_out.join(timeout=0.5)
            t_err.join(timeout=0.5)

        duration_ms = int((time.time() - start_ts) * 1000)

        if timed_out:
            # Preserve whatever we captured, but add a timeout message
            # to stderr.
            timeout_val = (
                timeout if timeout is not None else self.timeout
            )
            msg = f"Command timed out after {timeout_val} seconds\n"
            if on_stderr:
                on_stderr(msg)
            err_bytes = _append_capped(cap_err, msg, err_bytes)
            exit_code = 1
        else:
            exit_code = proc.returncode if proc.returncode is not None else 1

        # Normalize exit code 127 (command not found) to 1 for consistency
        if exit_code == 127:
            exit_code = 1

        return StreamResult(
            exit_code=exit_code,
            stdout="".join(cap_out),
            stderr="".join(cap_err),
            started_at=started_at,
            duration_ms=duration_ms,
            stdout_bytes=out_bytes,
            stderr_bytes=err_bytes,
            truncated=truncated,
        )

    def run_pty(
        self,
        command: str,
        on_output: Callable[[str], None] | None = None,
        timeout: int | None = None,
        cwd: str | None = None,
    ) -> StreamResult:
        """Run a command attached to a pseudo-terminal (PTY).

        Use this for interactive programs that require a TTY:
        - curses UIs
        - password prompts
        - tools that change behavior when not in a terminal

        Notes:
        - This is a foundational "TTY path". Kernel/UI can later implement
          full interactive handoff (forwarding keystrokes). For now this
          streams PTY output to on_output.

        Args:
            command: shell command to execute
            on_output: callback for PTY output
            timeout: overrides self.timeout
            cwd: working directory for the command (default: current directory)

        Returns:
            StreamResult (stdout contains all captured PTY text;
                stderr usually empty)
        """
        import os as _os
        import pty
        import select

        env = self._build_env()
        started_at = datetime.now().isoformat()
        start_ts = time.time()

        cap: list[str] = []
        total_bytes = 0
        truncated = False
        max_bytes = max(0, int(self.max_capture_bytes))
        deadline = start_ts + (
            timeout if timeout is not None else self.timeout
        )

        def _append_capped(s: str, current_bytes: int) -> int:
            nonlocal truncated
            b = len(s.encode("utf-8", errors="replace"))
            if max_bytes == 0 or current_bytes >= max_bytes:
                truncated = True
                return current_bytes + b
            remaining = max_bytes - current_bytes
            if b > remaining:
                raw = s.encode("utf-8", errors="replace")[:remaining]
                cap.append(raw.decode("utf-8", errors="replace"))
                truncated = True
                return current_bytes + b
            cap.append(s)
            return current_bytes + b

        master_fd, slave_fd = pty.openpty()

        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                cwd=cwd,
                # So we can signal the whole process group
                preexec_fn=_os.setsid,
            )
        except Exception as e:
            try:
                _os.close(master_fd)
                _os.close(slave_fd)
            except Exception:
                pass
            duration_ms = int((time.time() - start_ts) * 1000)
            return StreamResult(
                exit_code=1,
                stdout="",
                stderr=f"Error executing command: {e}",
                started_at=started_at,
                duration_ms=duration_ms,
                stdout_bytes=0,
                stderr_bytes=0,
                truncated=False,
            )
        finally:
            try:
                _os.close(slave_fd)
            except Exception:
                pass

        timed_out = False
        try:
            while True:
                if proc.poll() is not None:
                    # Drain remaining PTY output
                    while True:
                        r, _, _ = select.select([master_fd], [], [], 0)
                        if not r:
                            break
                        try:
                            data = _os.read(master_fd, 4096)
                        except OSError:
                            break
                        if not data:
                            break
                        chunk = data.decode("utf-8", errors="replace")
                        if on_output:
                            on_output(chunk)
                        total_bytes = _append_capped(chunk, total_bytes)
                    break

                if time.time() >= deadline:
                    timed_out = True
                    break

                r, _, _ = select.select([master_fd], [], [], 0.05)
                if r:
                    try:
                        data = _os.read(master_fd, 4096)
                    except OSError:
                        break
                    if data:
                        chunk = data.decode("utf-8", errors="replace")
                        if on_output:
                            on_output(chunk)
                        total_bytes = _append_capped(chunk, total_bytes)

        finally:
            if timed_out:
                try:
                    # Terminate entire process group
                    os.killpg(proc.pid, signal.SIGTERM)
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass

            try:
                os.close(master_fd)
            except Exception:
                pass

        duration_ms = int((time.time() - start_ts) * 1000)

        if timed_out:
            timeout_val = (
                timeout if timeout is not None else self.timeout
            )
            msg = f"\nCommand timed out after {timeout_val} seconds\n"
            if on_output:
                on_output(msg)
            total_bytes = _append_capped(msg, total_bytes)
            exit_code = 1
        else:
            exit_code = proc.returncode if proc.returncode is not None else 1

        if exit_code == 127:
            exit_code = 1

        return StreamResult(
            exit_code=exit_code,
            stdout="".join(cap),
            stderr="",
            started_at=started_at,
            duration_ms=duration_ms,
            stdout_bytes=total_bytes,
            stderr_bytes=0,
            truncated=truncated,
        )

    def run_tty(
        self, command: str, timeout: int | None = None, cwd: str | None = None
    ) -> TTYResult:
        """Run a command with full terminal control (no output capture).

        This mode allows pagers (less, man) and interactive programs to work
        properly by giving them direct access to the real TTY.

        The command inherits stdin/stdout/stderr from the parent process.
        No output is captured - the program takes over the terminal completely.

        Args:
            command: shell command to execute
            timeout: overrides self.timeout
            cwd: working directory for the command (default: current directory)

        Returns:
            TTYResult (exit_code, started_at, duration_ms)
        """
        env = self._build_env()
        started_at = datetime.now().isoformat()
        start_ts = time.time()

        deadline = start_ts + (
            timeout if timeout is not None else self.timeout
        )

        try:
            # Run with full terminal control - inherit stdin/stdout/stderr
            proc = subprocess.Popen(
                command,
                shell=True,
                stdin=None,  # inherit from parent
                stdout=None,  # inherit from parent
                stderr=None,  # inherit from parent
                env=env,
                cwd=cwd,
            )

            # Wait for completion with timeout
            timed_out = False
            while True:
                rc = proc.poll()
                if rc is not None:
                    break
                if time.time() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.05)

            if timed_out:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                exit_code = 1
            else:
                exit_code = (
                    proc.returncode if proc.returncode is not None else 1
                )

        except Exception:
            duration_ms = int((time.time() - start_ts) * 1000)
            return TTYResult(
                exit_code=1,
                started_at=started_at,
                duration_ms=duration_ms,
            )

        duration_ms = int((time.time() - start_ts) * 1000)

        # Normalize exit code 127 (command not found) to 1 for consistency
        if exit_code == 127:
            exit_code = 1

        return TTYResult(
            exit_code=exit_code,
            started_at=started_at,
            duration_ms=duration_ms,
        )
