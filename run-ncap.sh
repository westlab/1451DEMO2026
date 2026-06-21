#!/usr/bin/env bash
# Start the IEEE 1451.1.6 NCAP (NCAP.py).
#
# Usage:
#   ./run-ncap.sh              # pseudo hardware + verbose (any PC) -> default
#   ./run-ncap.sh -p -v -a     # pseudo, verbose, periodic announcements
#   ./run-ncap.sh -v -a        # real DHT11/servo on a Raspberry Pi
#
# Any arguments are passed straight to NCAP.py. Logs go to $NCAP_LOG
# (default /tmp/ncap.log). A previous instance is stopped first.

cd "$(dirname "$0")" || exit 1

# stop any running NCAP
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
    echo "NCAP started: pid $PID | args: $ARGS | log: $LOG"
    grep -m1 'NCAP up' "$LOG" 2>/dev/null
    echo "  request topics: see config.yml (default _1451.1.6/C|D0/PTTEST/ncap0)"
    echo "  stop with:  pkill -f 'python3 -u NCAP.py'"
else
    echo "NCAP failed to start -- last log lines:"
    tail -n 8 "$LOG"
    exit 1
fi
