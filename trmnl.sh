#!/bin/bash

# Configuration
APP_NAME="trmnl"
SCRIPT_PATH="/usr/src/trmnl/trmnl.py"
LOG_FILE="/usr/src/trmnl/nohup.out"
CACHE_DIR="/usr/src/trmnl/content/cache"

reset_cache() {
    if [ -d "$CACHE_DIR" ]; then
        find "$CACHE_DIR" -mindepth 1 -delete
        echo "Cleared application cache."
    fi
}

start() {
    if pgrep -f "python $SCRIPT_PATH" > /dev/null 2>&1; then
        echo "$APP_NAME is already running."
    else
        echo "Starting $APP_NAME..."
        reset_cache
        nohup python "$SCRIPT_PATH" > "$LOG_FILE" 2>&1 &
        echo "$APP_NAME started."
    fi
}

stop() {
    if pgrep -f "python $SCRIPT_PATH" > /dev/null 2>&1; then
        echo "Stopping $APP_NAME..."
        pkill -f "python $SCRIPT_PATH"
        echo "$APP_NAME stopped."
    fi
}

status() {
    if pgrep -f "python $SCRIPT_PATH" > /dev/null 2>&1; then
        echo "$APP_NAME is running (PID: $(pgrep -f "python $SCRIPT_PATH" | head -1))."
    else
        echo "$APP_NAME is stopped."
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
