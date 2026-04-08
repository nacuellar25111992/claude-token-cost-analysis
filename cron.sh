#!/bin/bash
# Daily Claude Code token usage report — HTML email sent by main.py via Mail.app

DIR="$HOME/.claude/token-cost-analysis"
PYTHON="$DIR/.venv/bin/python3"
TODAY=$(date +"%Y-%m-%d")
SUBJECT="Claude Code Token Report — $TODAY"

# Cargar variables de entorno (.env no usa export, set -a las auto-exporta)
set -a
source "$DIR/.env"
set +a

OUTPUT=$(cd "$DIR" && "$PYTHON" -m src.script.main 2>&1)
EXIT_CODE=$?

# On success, main.py sends the HTML email itself.
# On failure, send a plain-text error email so we know something broke.
if [ $EXIT_CODE -ne 0 ]; then
    SAFE_SUBJECT=$(printf '%s' "$SUBJECT" | sed 's/\\/\\\\/g; s/"/\\"/g')
    SAFE_OUTPUT=$(printf '%s' "$OUTPUT" | sed 's/\\/\\\\/g; s/"/\\"/g')
    SAFE_RECIPIENT=$(printf '%s' "$EMAIL_RECIPIENT" | sed 's/\\/\\\\/g; s/"/\\"/g')

    osascript <<EOF
tell application "Mail"
    set newMsg to make new outgoing message with properties {subject:"[ERROR] $SAFE_SUBJECT", content:"Error running token analyzer (exit code $EXIT_CODE):\n\n$SAFE_OUTPUT", visible:false}
    tell newMsg
        make new to recipient with properties {address:"$SAFE_RECIPIENT"}
    end tell
    send newMsg
end tell
EOF
fi
