#!/bin/bash

# Configuration
APP_NAME="trmnl"
SCRIPT_PATH="/usr/src/trmnl/trmnl.py"
LOG_FILE="/usr/src/trmnl/nohup.out"
CACHE_DIR="/usr/src/trmnl/content/cache"
STOP_TIMEOUT=30

app_pids() {
    pgrep -f "python $SCRIPT_PATH" || true
}

reset_cache() {
    if [ -d "$CACHE_DIR" ]; then
        find "$CACHE_DIR" -mindepth 1 -delete
        echo "Cleared application cache."
    fi
}

start() {
    if app_pids | grep -q .; then
        echo "$APP_NAME is already running."
    else
        echo "Starting $APP_NAME..."
        reset_cache
        nohup python "$SCRIPT_PATH" > "$LOG_FILE" 2>&1 &
        echo "$APP_NAME started."
    fi
}

stop() {
    local pids
    mapfile -t pids < <(app_pids)
    if [ "${#pids[@]}" -gt 0 ]; then
        echo "Stopping $APP_NAME..."
        kill -TERM "${pids[@]}"

        local waited=0
        while app_pids | grep -q . && [ "$waited" -lt "$STOP_TIMEOUT" ]; do
            sleep 1
            ((waited += 1))
        done

        if app_pids | grep -q .; then
            echo "$APP_NAME did not stop within ${STOP_TIMEOUT}s; sending SIGKILL."
            mapfile -t pids < <(app_pids)
            if [ "${#pids[@]}" -gt 0 ]; then
                kill -KILL "${pids[@]}"
            fi
            while app_pids | grep -q .; do
                sleep 1
            done
        fi
        echo "$APP_NAME stopped."
    fi
}

status() {
    if app_pids | grep -q .; then
        echo "$APP_NAME is running (PID: $(app_pids | head -1))."
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
