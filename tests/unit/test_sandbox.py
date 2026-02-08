"""Unit tests for the sandbox module (base, docker_executor, e2b_executor, manager).

All tests mock external dependencies (Docker SDK, E2B SDK, filesystem) so that
no real containers or cloud sandboxes are created.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.sandbox.base import ExecutionResult, SandboxExecutor
from src.sandbox.docker_executor import DockerExecutor
from src.sandbox.e2b_executor import E2BExecutor
from src.sandbox.manager import SandboxManager

# ===================================================================
# ExecutionResult tests
# ===================================================================


class TestExecutionResult:
    """Tests for the ExecutionResult dataclass."""

    def test_success_when_zero_exit_code_and_no_timeout(self):
        result = ExecutionResult(exit_code=0, timed_out=False)
        assert result.success is True

    def test_failure_when_nonzero_exit_code(self):
        result = ExecutionResult(exit_code=1, timed_out=False)
        assert result.success is False

    def test_failure_when_timed_out(self):
        result = ExecutionResult(exit_code=0, timed_out=True)
        assert result.success is False

    def test_failure_when_both_bad(self):
        result = ExecutionResult(exit_code=137, timed_out=True)
        assert result.success is False

    def test_default_values(self):
        result = ExecutionResult()
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.duration_ms == 0.0
        assert result.files_created == []
        assert result.success is True

    def test_files_created_default_is_independent(self):
        """Ensure default list is not shared across instances."""
        r1 = ExecutionResult()
        r2 = ExecutionResult()
        r1.files_created.append("foo.txt")
        assert r2.files_created == []


# ===================================================================
# SandboxExecutor ABC tests
# ===================================================================


class TestSandboxExecutorABC:
    """Ensure SandboxExecutor cannot be instantiated directly."""

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            SandboxExecutor()  # type: ignore[abstract]


# ===================================================================
# DockerExecutor tests
# ===================================================================


class TestDockerExecutor:
    """Tests for DockerExecutor with mocked Docker SDK."""

    def test_default_image(self):
        executor = DockerExecutor()
        assert executor._image == "mas-sandbox:latest"

    def test_custom_image(self):
        executor = DockerExecutor(image="my-image:v2")
        assert executor._image == "my-image:v2"

    def test_detect_language_python(self):
        files = [{"path": "main.py", "content": "print('hi')"}]
        assert DockerExecutor._detect_language(files) == "python"

    def test_detect_language_javascript(self):
        files = [{"path": "index.js", "content": "console.log('hi')"}]
        assert DockerExecutor._detect_language(files) == "javascript"

    def test_detect_language_typescript(self):
        files = [{"path": "app.ts", "content": "const x: number = 1"}]
        assert DockerExecutor._detect_language(files) == "typescript"

    def test_detect_language_mixed_majority_wins(self):
        files = [
            {"path": "a.py", "content": ""},
            {"path": "b.py", "content": ""},
            {"path": "c.js", "content": ""},
        ]
        assert DockerExecutor._detect_language(files) == "python"

    def test_detect_language_unknown_defaults_to_python(self):
        files = [{"path": "readme.md", "content": "# Hello"}]
        assert DockerExecutor._detect_language(files) == "python"

    def test_build_command_python_main(self):
        files = [{"path": "main.py", "content": "print(1)"}]
        cmd = DockerExecutor._build_command(files, "python")
        assert cmd == "python main.py"

    def test_build_command_python_first_py_file(self):
        files = [{"path": "app.py", "content": "print(1)"}]
        cmd = DockerExecutor._build_command(files, "python")
        assert cmd == "python app.py"

    def test_build_command_js_index(self):
        files = [{"path": "index.js", "content": "console.log(1)"}]
        cmd = DockerExecutor._build_command(files, "javascript")
        assert cmd == "node index.js"

    def test_build_command_ts_fallback(self):
        files = [{"path": "app.ts", "content": "const x = 1"}]
        cmd = DockerExecutor._build_command(files, "typescript")
        assert cmd == "npx ts-node app.ts"

    def test_build_command_fallback_to_first_file(self):
        files = [{"path": "data.csv", "content": "a,b"}]
        cmd = DockerExecutor._build_command(files, "python")
        assert cmd == "python data.csv"

    async def test_execute_no_files_returns_error(self):
        executor = DockerExecutor()
        result = await executor.execute(files=[])
        assert result.exit_code == 1
        assert "No files" in result.stderr

    @patch("src.sandbox.docker_executor.DockerExecutor._run_container")
    @patch("src.sandbox.docker_executor.DockerExecutor._capture_logs")
    @patch("src.sandbox.docker_executor.DockerExecutor._get_exit_code")
    @patch("src.sandbox.docker_executor.DockerExecutor._cleanup_container")
    async def test_execute_success(
        self, mock_cleanup, mock_exit, mock_logs, mock_run
    ):
        mock_container = MagicMock()
        mock_run.return_value = (mock_container, False)
        mock_logs.return_value = ("Hello World\n", "")
        mock_exit.return_value = 0
        mock_cleanup.return_value = None

        executor = DockerExecutor()

        with patch("tempfile.mkdtemp", return_value="/tmp/test_sandbox"):
            with patch.object(DockerExecutor, "_write_files"):
                with patch.object(DockerExecutor, "_list_created_files", return_value=[]):
                    with patch("shutil.rmtree"):
                        result = await executor.execute(
                            files=[{"path": "main.py", "content": "print('Hello World')"}],
                            timeout_seconds=30,
                        )

        assert result.success is True
        assert result.stdout == "Hello World\n"
        assert result.exit_code == 0
        assert result.timed_out is False

    @patch("src.sandbox.docker_executor.DockerExecutor._run_container")
    @patch("src.sandbox.docker_executor.DockerExecutor._capture_logs")
    @patch("src.sandbox.docker_executor.DockerExecutor._get_exit_code")
    @patch("src.sandbox.docker_executor.DockerExecutor._cleanup_container")
    async def test_execute_timeout(
        self, mock_cleanup, mock_exit, mock_logs, mock_run
    ):
        mock_container = MagicMock()
        mock_run.return_value = (mock_container, True)  # timed_out=True
        mock_logs.return_value = ("partial", "")
        mock_exit.return_value = 137
        mock_cleanup.return_value = None

        executor = DockerExecutor()

        with patch("tempfile.mkdtemp", return_value="/tmp/test_sandbox"):
            with patch.object(DockerExecutor, "_write_files"):
                with patch.object(DockerExecutor, "_list_created_files", return_value=[]):
                    with patch("shutil.rmtree"):
                        result = await executor.execute(
                            files=[{"path": "main.py", "content": "while True: pass"}],
                            timeout_seconds=5,
                        )

        assert result.timed_out is True
        assert result.exit_code == 137
        assert result.success is False

    @patch("src.sandbox.docker_executor.DockerExecutor._run_container")
    @patch("src.sandbox.docker_executor.DockerExecutor._capture_logs")
    @patch("src.sandbox.docker_executor.DockerExecutor._get_exit_code")
    @patch("src.sandbox.docker_executor.DockerExecutor._cleanup_container")
    async def test_execute_nonzero_exit(
        self, mock_cleanup, mock_exit, mock_logs, mock_run
    ):
        mock_container = MagicMock()
        mock_run.return_value = (mock_container, False)
        mock_logs.return_value = ("", "NameError: name 'foo' is not defined\n")
        mock_exit.return_value = 1
        mock_cleanup.return_value = None

        executor = DockerExecutor()

        with patch("tempfile.mkdtemp", return_value="/tmp/test_sandbox"):
            with patch.object(DockerExecutor, "_write_files"):
                with patch.object(DockerExecutor, "_list_created_files", return_value=[]):
                    with patch("shutil.rmtree"):
                        result = await executor.execute(
                            files=[{"path": "main.py", "content": "foo"}],
                        )

        assert result.success is False
        assert result.exit_code == 1
        assert "NameError" in result.stderr

    async def test_cleanup_is_noop(self):
        executor = DockerExecutor()
        await executor.cleanup()  # Should not raise.

    def test_write_files_creates_files(self, tmp_path):
        files = [
            {"path": "main.py", "content": "print('hello')"},
            {"path": "lib/utils.py", "content": "def add(a, b): return a + b"},
        ]
        DockerExecutor._write_files(str(tmp_path), files)

        assert (tmp_path / "main.py").read_text() == "print('hello')"
        assert (tmp_path / "lib" / "utils.py").read_text() == "def add(a, b): return a + b"

    def test_write_files_blocks_path_traversal(self, tmp_path):
        files = [{"path": "../../etc/passwd", "content": "evil"}]
        DockerExecutor._write_files(str(tmp_path), files)

        # Should NOT create ../../etc/passwd outside tmp_path.
        assert not (tmp_path.parent.parent / "etc" / "passwd").exists()
        # The sanitiser strips ".." segments; the remaining path "etc/passwd"
        # is written safely inside the tmp directory.
        written = tmp_path / "etc" / "passwd"
        assert written.exists()
        assert written.resolve().is_relative_to(tmp_path.resolve())

    def test_list_created_files(self, tmp_path):
        # Original files.
        (tmp_path / "main.py").write_text("print(1)")
        # Simulated output file.
        (tmp_path / "output.txt").write_text("result")

        original = [{"path": "main.py", "content": "print(1)"}]
        created = DockerExecutor._list_created_files(str(tmp_path), original)

        assert "output.txt" in created
        assert "main.py" not in created


# ===================================================================
# E2BExecutor tests
# ===================================================================


class TestE2BExecutor:
    """Tests for E2BExecutor with mocked E2B SDK."""

    def test_init_without_api_key_warns(self):
        """Constructor should not raise even without API key."""
        with patch.dict(os.environ, {}, clear=False):
            # Remove E2B_API_KEY if it exists.
            env = dict(os.environ)
            env.pop("E2B_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                executor = E2BExecutor(api_key="")
                assert executor._api_key == ""

    def test_init_with_explicit_key(self):
        executor = E2BExecutor(api_key="test-key-123")
        assert executor._api_key == "test-key-123"

    def test_init_reads_env_var(self):
        with patch.dict(os.environ, {"E2B_API_KEY": "env-key-456"}):
            executor = E2BExecutor()
            assert executor._api_key == "env-key-456"

    @patch("src.sandbox.e2b_executor._E2B_AVAILABLE", True)
    async def test_execute_no_files_returns_error(self):
        executor = E2BExecutor(api_key="test-key")
        result = await executor.execute(files=[])
        assert result.exit_code == 1
        assert "No files" in result.stderr

    @patch("src.sandbox.e2b_executor._E2B_AVAILABLE", True)
    async def test_execute_no_api_key_returns_error(self):
        with patch.dict(os.environ, {}, clear=False):
            env = dict(os.environ)
            env.pop("E2B_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                executor = E2BExecutor(api_key="")
                result = await executor.execute(
                    files=[{"path": "main.py", "content": "print(1)"}]
                )
                assert result.exit_code == 1
                assert "API key" in result.stderr

    async def test_execute_raises_when_sdk_missing(self):
        """When E2B SDK is not installed, execute raises ImportError."""
        with patch("src.sandbox.e2b_executor._E2B_AVAILABLE", False):
            executor = E2BExecutor(api_key="test-key")
            with pytest.raises(ImportError, match="e2b-code-interpreter"):
                await executor.execute(files=[{"path": "main.py", "content": "print(1)"}])

    @patch("src.sandbox.e2b_executor._E2B_AVAILABLE", True)
    @patch("src.sandbox.e2b_executor.AsyncSandbox")
    async def test_execute_success(self, mock_sandbox_cls):
        mock_sandbox = AsyncMock()
        mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)
        mock_sandbox.filesystem.write = AsyncMock()
        mock_process_result = MagicMock()
        mock_process_result.stdout = "42\n"
        mock_process_result.stderr = ""
        mock_process_result.exit_code = 0
        mock_sandbox.process.start_and_wait = AsyncMock(return_value=mock_process_result)
        mock_sandbox.close = AsyncMock()

        executor = E2BExecutor(api_key="test-key")
        result = await executor.execute(
            files=[{"path": "main.py", "content": "print(42)"}],
            timeout_seconds=30,
        )

        assert result.success is True
        assert result.stdout == "42\n"
        assert result.exit_code == 0
        mock_sandbox.filesystem.write.assert_called_once_with("/home/user/main.py", "print(42)")

    @patch("src.sandbox.e2b_executor._E2B_AVAILABLE", True)
    @patch("src.sandbox.e2b_executor.AsyncSandbox")
    async def test_execute_failure(self, mock_sandbox_cls):
        mock_sandbox = AsyncMock()
        mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)
        mock_sandbox.filesystem.write = AsyncMock()
        mock_process_result = MagicMock()
        mock_process_result.stdout = ""
        mock_process_result.stderr = "SyntaxError: invalid syntax\n"
        mock_process_result.exit_code = 1
        mock_sandbox.process.start_and_wait = AsyncMock(return_value=mock_process_result)
        mock_sandbox.close = AsyncMock()

        executor = E2BExecutor(api_key="test-key")
        result = await executor.execute(
            files=[{"path": "main.py", "content": "def :"}],
        )

        assert result.success is False
        assert result.exit_code == 1
        assert "SyntaxError" in result.stderr

    @patch("src.sandbox.e2b_executor._E2B_AVAILABLE", True)
    @patch("src.sandbox.e2b_executor.AsyncSandbox")
    async def test_execute_timeout(self, mock_sandbox_cls):
        mock_sandbox = AsyncMock()
        mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)
        mock_sandbox.filesystem.write = AsyncMock()
        mock_sandbox.process.start_and_wait = AsyncMock(side_effect=TimeoutError("timed out"))
        mock_sandbox.close = AsyncMock()

        executor = E2BExecutor(api_key="test-key")
        result = await executor.execute(
            files=[{"path": "main.py", "content": "while True: pass"}],
            timeout_seconds=5,
        )

        assert result.timed_out is True
        assert result.exit_code == 137

    @patch("src.sandbox.e2b_executor._E2B_AVAILABLE", True)
    @patch("src.sandbox.e2b_executor.AsyncSandbox")
    async def test_cleanup_closes_sandbox(self, mock_sandbox_cls):
        mock_sandbox = AsyncMock()
        mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)
        mock_sandbox.close = AsyncMock()

        executor = E2BExecutor(api_key="test-key")
        executor._sandbox = mock_sandbox
        await executor.cleanup()

        mock_sandbox.close.assert_called_once()
        assert executor._sandbox is None

    def test_build_command_python(self):
        files = [{"path": "main.py", "content": "print(1)"}]
        assert E2BExecutor._build_command(files) == "python main.py"

    def test_build_command_js(self):
        files = [{"path": "index.js", "content": "console.log(1)"}]
        assert E2BExecutor._build_command(files) == "node index.js"

    def test_build_command_ts(self):
        files = [{"path": "index.ts", "content": "const x = 1"}]
        assert E2BExecutor._build_command(files) == "npx ts-node index.ts"

    def test_build_command_unknown_falls_back_to_python(self):
        files = [{"path": "data.csv", "content": "a,b"}]
        cmd = E2BExecutor._build_command(files)
        assert cmd == "python data.csv"


# ===================================================================
# SandboxManager tests
# ===================================================================


class TestSandboxManager:
    """Tests for SandboxManager routing logic."""

    def test_default_prefers_docker(self):
        mgr = SandboxManager()
        assert mgr._prefer_docker is True

    def test_choose_order_docker_first(self):
        mgr = SandboxManager(prefer_docker=True, e2b_api_key="key")
        primary, fallback = mgr._choose_order()
        assert primary == "docker"
        assert fallback == "e2b"

    def test_choose_order_docker_first_no_e2b(self):
        mgr = SandboxManager(prefer_docker=True, e2b_api_key="")
        primary, fallback = mgr._choose_order()
        assert primary == "docker"
        assert fallback is None

    def test_choose_order_e2b_first(self):
        mgr = SandboxManager(prefer_docker=False, e2b_api_key="key")
        primary, fallback = mgr._choose_order()
        assert primary == "e2b"
        assert fallback == "docker"

    async def test_execute_code_no_files_returns_error(self):
        mgr = SandboxManager()
        result = await mgr.execute_code(files=[])
        assert result.exit_code == 1
        assert "No files" in result.stderr

    @patch("src.sandbox.manager.SandboxManager._check_docker_available")
    async def test_execute_code_docker_success(self, mock_docker_check):
        mock_docker_check.return_value = None

        mock_exec = AsyncMock(
            return_value=ExecutionResult(stdout="OK", exit_code=0, duration_ms=100.0)
        )

        mgr = SandboxManager()

        with patch.object(DockerExecutor, "execute", mock_exec):
            result = await mgr.execute_code(
                files=[{"path": "main.py", "content": "print('OK')"}]
            )

        assert result.success is True
        assert result.stdout == "OK"

    @patch("src.sandbox.manager.SandboxManager._check_docker_available")
    async def test_execute_code_docker_fails_falls_back_to_e2b(self, mock_docker_check):
        # Docker is available but execution fails.
        mock_docker_check.return_value = None
        docker_exec = AsyncMock(side_effect=RuntimeError("Docker exploded"))
        e2b_exec = AsyncMock(
            return_value=ExecutionResult(stdout="e2b ok", exit_code=0, duration_ms=200.0)
        )

        mgr = SandboxManager(prefer_docker=True, e2b_api_key="test-key")

        with patch.object(DockerExecutor, "execute", docker_exec):
            with patch.object(E2BExecutor, "execute", e2b_exec):
                result = await mgr.execute_code(
                    files=[{"path": "main.py", "content": "print(1)"}]
                )

        assert result.success is True
        assert result.stdout == "e2b ok"

    async def test_execute_code_all_unavailable(self):
        """Both Docker and E2B unavailable returns error result."""
        with patch("src.sandbox.manager.SandboxManager._check_docker_available", side_effect=RuntimeError("no docker")):
            mgr = SandboxManager(prefer_docker=True, e2b_api_key="")
            result = await mgr.execute_code(
                files=[{"path": "main.py", "content": "print(1)"}]
            )

        assert result.success is False
        assert "No sandbox backend available" in result.stderr

    @patch("src.sandbox.manager.SandboxManager._check_docker_available")
    async def test_cleanup_calls_both(self, mock_docker_check):
        mock_docker_check.return_value = None

        mgr = SandboxManager(e2b_api_key="test-key")

        # Force creation of both executors.
        mgr._docker = AsyncMock()
        mgr._docker.cleanup = AsyncMock()
        mgr._e2b = AsyncMock()
        mgr._e2b.cleanup = AsyncMock()

        await mgr.cleanup()

        mgr._docker.cleanup.assert_called_once()
        mgr._e2b.cleanup.assert_called_once()

    async def test_get_docker_returns_none_when_daemon_down(self):
        mock_target = "src.sandbox.manager.SandboxManager._check_docker_available"
        with patch(mock_target, side_effect=RuntimeError("daemon off")):
            mgr = SandboxManager()
            assert mgr._get_docker() is None

    def test_get_e2b_returns_none_without_key(self):
        with patch.dict(os.environ, {}, clear=False):
            env = dict(os.environ)
            env.pop("E2B_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                mgr = SandboxManager(e2b_api_key="")
                assert mgr._get_e2b() is None

    def test_get_e2b_returns_executor_with_key(self):
        mgr = SandboxManager(e2b_api_key="test-key")
        executor = mgr._get_e2b()
        assert isinstance(executor, E2BExecutor)


# ===================================================================
# Docker container security constraints tests
# ===================================================================


class TestDockerSecurityConstraints:
    """Verify that Docker containers are created with the right security settings.

    The ``docker`` package is imported lazily inside ``_run_container``, so we
    patch it via ``sys.modules`` and ``importlib`` rather than patching a
    module-level attribute.
    """

    @staticmethod
    def _make_mock_docker():
        """Create a mock ``docker`` module with a usable containers.run."""
        mock_docker = MagicMock()
        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client

        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = b""
        mock_container.attrs = {"State": {"ExitCode": 0}}
        mock_container.remove.return_value = None
        mock_client.containers.run.return_value = mock_container

        return mock_docker, mock_client, mock_container

    async def test_container_uses_network_none(self):
        """CRITICAL: containers must have network_mode='none'."""
        mock_docker, mock_client, _ = self._make_mock_docker()

        with patch.dict("sys.modules", {"docker": mock_docker}):
            executor = DockerExecutor()
            await executor._run_container(
                tmp_dir="/tmp/test_sandbox",
                command="python main.py",
                image="mas-sandbox:latest",
                timeout_seconds=30,
            )

        call_kwargs = mock_client.containers.run.call_args
        assert call_kwargs.kwargs.get("network_mode") == "none" or call_kwargs[1].get("network_mode") == "none"

    async def test_container_uses_non_root_user(self):
        mock_docker, mock_client, _ = self._make_mock_docker()

        with patch.dict("sys.modules", {"docker": mock_docker}):
            executor = DockerExecutor()
            await executor._run_container(
                tmp_dir="/tmp/test",
                command="python main.py",
                image="mas-sandbox:latest",
                timeout_seconds=10,
            )

        call_kwargs = mock_client.containers.run.call_args
        user_arg = call_kwargs.kwargs.get("user") or call_kwargs[1].get("user")
        assert user_arg == "1000:1000"

    async def test_container_has_memory_limit(self):
        mock_docker, mock_client, _ = self._make_mock_docker()

        with patch.dict("sys.modules", {"docker": mock_docker}):
            executor = DockerExecutor()
            await executor._run_container(
                tmp_dir="/tmp/test",
                command="python main.py",
                image="mas-sandbox:latest",
                timeout_seconds=10,
            )

        call_kwargs = mock_client.containers.run.call_args
        mem_limit = call_kwargs.kwargs.get("mem_limit") or call_kwargs[1].get("mem_limit")
        assert mem_limit == "512m"

    async def test_container_has_cpu_limit(self):
        mock_docker, mock_client, _ = self._make_mock_docker()

        with patch.dict("sys.modules", {"docker": mock_docker}):
            executor = DockerExecutor()
            await executor._run_container(
                tmp_dir="/tmp/test",
                command="python main.py",
                image="mas-sandbox:latest",
                timeout_seconds=10,
            )

        call_kwargs = mock_client.containers.run.call_args
        nano_cpus = call_kwargs.kwargs.get("nano_cpus") or call_kwargs[1].get("nano_cpus")
        assert nano_cpus == 1_000_000_000
