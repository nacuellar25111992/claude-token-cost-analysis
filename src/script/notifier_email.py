"""HTML email sender via Mail.app / osascript."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class EmailNotifier:
    """Sends an HTML email via Mail.app using AppleScript."""

    def __init__(self, recipient: str):
        self._recipient = recipient

    def send(self, subject: str, html_body: str) -> None:
        """
        Write HTML to a secure temp file and send via Mail.app.
        Temp file is removed after sending (or on failure).
        """
        fd, tmp_path = tempfile.mkstemp(prefix="claude_token_", suffix=".txt")
        self._tmp = Path(tmp_path)
        try:
            os.close(fd)
            self._tmp.write_text(html_body, encoding="utf-8")

            safe_subject = subject.replace("\\", "\\\\").replace('"', '\\"')
            safe_recipient = self._recipient.replace("\\", "\\\\").replace('"', '\\"')
            safe_tmp = str(self._tmp).replace("\\", "\\\\").replace('"', '\\"')

            script = f"""
set htmlContent to (do shell script "cat " & quoted form of "{safe_tmp}")
tell application "Mail"
    set newMsg to make new outgoing message with properties {{subject:"{safe_subject}", content:htmlContent, visible:false}}
    tell newMsg
        make new to recipient with properties {{address:"{safe_recipient}"}}
    end tell
    send newMsg
end tell
"""
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)

            if result.returncode != 0:
                raise RuntimeError(f"Mail.app send failed: {result.stderr.strip()}")

            print(f"HTML email sent to {self._recipient}")
        finally:
            self._tmp.unlink(missing_ok=True)
