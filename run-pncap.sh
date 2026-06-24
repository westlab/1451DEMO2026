#!/usr/bin/env bash
# Start the IEEE 1451.1.6 NCAP (NCAP.py) in PSEUDO mode (-p).
#
# Pseudo = no real GPIO needed: DHT11/servo are faked so the demo runs on any
# machine. NOTE: the M5 ENV(SCD41) path is ALWAYS real -- even in pseudo mode
# it shows real telemetry if the M5 units are publishing, or "no data" if not.
# It never fabricates M5 values.
#
# For real hardware / real M5 values, use ./run-ncap.sh instead.
# Stop everything with ./stop-ncap.sh
#
# Usage:
#   ./run-pncap.sh          # pseudo (-p -v) -> default
#   ./run-pncap.sh -p -v -a # pseudo, verbose, periodic announcements
# Any arguments are passed straight to NCAP.py. Logs go to $NCAP_LOG.

cd "$(dirname "$0")" || exit 1

# stop any running NCAP (real or pseudo)
if pkill -f "python3 -u NCAP.py" 2>/dev/null; then
    echo "stopped previous NCAP"
    sleep 1
fi

ARGS="${*:--p -v}"
LOG="${NCAP_LOG:-/tmp/ncap.log}"

nohup python3 -u NCAP.py $ARGS > "$LOG" 2>&1 &
PID=$!
sleep 2

if kill -0 "$PID" 2>/dev/null; then
    echo "NCAP started (PSEUDO): pid $PID | args: $ARGS | log: $LOG"
    grep -m1 'NCAP up' "$LOG" 2>/dev/null
    echo "  request topics: see config.yml (default _1451.1.6/C|D0/PTTEST/ncap0)"
    echo "  stop with:  ./stop-ncap.sh"
else
    echo "NCAP failed to start -- last log lines:"
    tail -n 8 "$LOG"
    exit 1
fi
