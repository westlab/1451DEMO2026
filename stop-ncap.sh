#!/usr/bin/env bash
# Stop the IEEE 1451.1.6 NCAP -- BOTH real (run-ncap.sh) and pseudo
# (run-pncap.sh), since both run the same "python3 -u NCAP.py" process.

if pkill -f "python3 -u NCAP.py" 2>/dev/null; then
    sleep 1
    if pgrep -f "python3 -u NCAP.py" >/dev/null 2>&1; then
        echo "NCAP still running, forcing..."
        pkill -9 -f "python3 -u NCAP.py" 2>/dev/null
        sleep 1
    fi
    echo "NCAP stopped."
else
    echo "no NCAP running."
fi
