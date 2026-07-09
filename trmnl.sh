#!/bin/bash

# Configuration
APP_NAME="trmnl"
SCRIPT_PATH="/usr/src/trmnl/trmnl.py"
PID_FILE="/tmp/${APP_NAME}.pid"
LOG_FILE="/usr/src/trmnl/nohup.out"

start() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "$APP_NAME is already running (PID: $(cat "$PID_FILE"))."
    else
        echo "Starting $APP_NAME..."
        nohup python "$SCRIPT_PATH" > "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        echo "$APP_NAME started with PID: $(cat "$PID_FILE")"
    fi
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        echo "Stopping $APP_NAME (PID: $PID)..."
        kill "$PID"
        rm "$PID_FILE"
        echo "$APP_NAME stopped."
    else
        # Fallback: search for process if PID file is missing
        PID=$(pgrep -f "python $SCRIPT_PATH")
        if [ -n "$PID" ]; then
            echo "Stopping $APP_NAME found by pgrep (PID: $PID)..."
            kill "$PID"
            echo "$APP_NAME stopped."
        else
            echo "$APP_NAME is not running."
        fi
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        echo "$APP_NAME is running (PID: $(cat "$PID_FILE"))."
    else
        PID=$(pgrep -f "python $SCRIPT_PATH")
        if [ -n "$PID" ]; then
            echo "$APP_NAME is running (PID: $PID) but PID file is missing."
        else
            echo "$APP_NAME is stopped."
        fi
    fi
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        stop
        sleep 1
        start
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
esac

exit 0
