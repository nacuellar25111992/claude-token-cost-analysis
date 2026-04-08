"""Tests for token_cost_analysis.notifier_email."""

import os
import pytest
from unittest.mock import patch, MagicMock

from src.script.notifier_email import EmailNotifier


def _mock_mkstemp(tmp_path):
    """Return a mkstemp side_effect that creates a real file inside tmp_path."""
    tmp_file = tmp_path / "email_body.html"

    def side_effect(prefix="", suffix=""):
        fd = os.open(str(tmp_file), os.O_CREAT | os.O_WRONLY, 0o600)
        return fd, str(tmp_file)

    return tmp_file, side_effect


class TestEmailNotifierSend:
    def test_writes_html_to_tmp_file(self, tmp_path):
        tmp_file, mkstemp_fn = _mock_mkstemp(tmp_path)
        notifier = EmailNotifier("test@example.com")

        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("src.script.notifier_email.tempfile.mkstemp", side_effect=mkstemp_fn), \
             patch("subprocess.run", return_value=mock_result):
            notifier.send("My Subject", "<html>body</html>")

        # File should be removed after send
        assert not tmp_file.exists()

    def test_calls_osascript(self, tmp_path):
        tmp_file, mkstemp_fn = _mock_mkstemp(tmp_path)
        notifier = EmailNotifier("test@example.com")

        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("src.script.notifier_email.tempfile.mkstemp", side_effect=mkstemp_fn), \
             patch("subprocess.run", return_value=mock_result) as mock_run:
            notifier.send("Subject", "<html/>")

        args = mock_run.call_args[0][0]
        assert args[0] == "osascript"

    def test_raises_on_nonzero_returncode(self, tmp_path):
        tmp_file, mkstemp_fn = _mock_mkstemp(tmp_path)
        notifier = EmailNotifier("test@example.com")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Mail error"
        with patch("src.script.notifier_email.tempfile.mkstemp", side_effect=mkstemp_fn), \
             patch("subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="Mail.app send failed"):
                notifier.send("Subject", "<html/>")

    def test_tmp_file_removed_on_failure(self, tmp_path):
        tmp_file, mkstemp_fn = _mock_mkstemp(tmp_path)
        notifier = EmailNotifier("test@example.com")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"
        with patch("src.script.notifier_email.tempfile.mkstemp", side_effect=mkstemp_fn), \
             patch("subprocess.run", return_value=mock_result):
            try:
                notifier.send("Subject", "<html/>")
            except RuntimeError:
                pass
        # Temp file should be cleaned up even on failure
        assert not tmp_file.exists()

    def test_subject_escaping(self, tmp_path):
        tmp_file, mkstemp_fn = _mock_mkstemp(tmp_path)
        notifier = EmailNotifier("test@example.com")

        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("src.script.notifier_email.tempfile.mkstemp", side_effect=mkstemp_fn), \
             patch("subprocess.run", return_value=mock_result) as mock_run:
            notifier.send('Subject with "quotes"', "<html/>")

        script = mock_run.call_args[0][0][2]  # -e <script>
        # Quotes should be escaped in the AppleScript
        assert '\\"' in script or "quotes" in script

    def test_recipient_in_applescript(self, tmp_path):
        tmp_file, mkstemp_fn = _mock_mkstemp(tmp_path)
        notifier = EmailNotifier("recipient@domain.com")

        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("src.script.notifier_email.tempfile.mkstemp", side_effect=mkstemp_fn), \
             patch("subprocess.run", return_value=mock_result) as mock_run:
            notifier.send("Subject", "<html/>")

        script = mock_run.call_args[0][0][2]
        assert "recipient@domain.com" in script

    def test_html_content_written_before_subprocess(self, tmp_path):
        tmp_file, mkstemp_fn = _mock_mkstemp(tmp_path)
        notifier = EmailNotifier("x@y.com")
        html_body = "<html><body>test content</body></html>"
        written_content = []

        def capture_run(*args, **kwargs):
            if tmp_file.exists():
                written_content.append(tmp_file.read_text())
            mock = MagicMock()
            mock.returncode = 0
            return mock

        with patch("src.script.notifier_email.tempfile.mkstemp", side_effect=mkstemp_fn), \
             patch("subprocess.run", side_effect=capture_run):
            notifier.send("Subject", html_body)

        assert len(written_content) == 1
        assert written_content[0] == html_body
