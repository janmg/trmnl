"""Crash diagnostics for the TRMNL server.

* trmnl.log: rotating log of INFO and above (requests, errors with tracebacks, heartbeat).
* trmnl-faults.log: Python stack dumps on fatal signals (SIGSEGV, SIGABRT, ...), e.g. a gunicorn worker timeout.
* Uncaught exceptions in the main thread or in any thread are logged before the process continues or exits.
* SIGTERM is logged, so a clean stop can be told apart from a crash or an OOM kill (SIGKILL leaves no trace).
* A heartbeat every HEARTBEAT_SECONDS records uptime, resident memory and thread count.
"""
import faulthandler
import logging
import os
import signal
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

HEARTBEAT_SECONDS = 600
LOG_FILE_NAME = 'trmnl.log'
FAULT_FILE_NAME = 'trmnl-faults.log'

logger = logging.getLogger('trmnl.diagnostics')

_fault_file = None
_started_at = time.monotonic()
_original_excepthook = sys.excepthook
_original_thread_excepthook = threading.excepthook


def _rss_mb():
    """Resident memory of this process in MB, or None where /proc is unavailable."""
    try:
        with open('/proc/self/status') as status:
            for line in status:
                if line.startswith('VmRSS:'):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return None


def _heartbeat():
    while True:
        time.sleep(HEARTBEAT_SECONDS)
        logger.info('heartbeat uptime=%ds rss=%sMB threads=%d',
                    time.monotonic() - _started_at, _rss_mb(), threading.active_count())


def _on_sigterm(signum, frame):
    logger.critical('Received SIGTERM, shutting down')
    # Restore the default action and re-send, so the process terminates as it would without this handler.
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def _log_uncaught(exc_type, exc, tb):
    if not issubclass(exc_type, KeyboardInterrupt):
        logger.critical('Uncaught exception in main thread', exc_info=(exc_type, exc, tb))
    _original_excepthook(exc_type, exc, tb)


def _log_thread_exception(args):
    if args.exc_type is not SystemExit:
        logger.critical('Uncaught exception in thread %s', getattr(args.thread, 'name', None),
                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    _original_thread_excepthook(args)


def setup_diagnostics(base_dir):
    """Install logging and crash hooks; log files are written to base_dir."""
    global _fault_file

    formatter = logging.Formatter(
        '%(asctime)s %(levelname)s pid=%(process)d [%(threadName)s] %(name)s: %(message)s')

    file_handler = RotatingFileHandler(os.path.join(base_dir, LOG_FILE_NAME),
                                       maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
    file_handler.setFormatter(formatter)

    # Warnings and errors also go to stderr, so they still show up in nohup.out.
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)

    # Kept open for the lifetime of the process: faulthandler writes to this file descriptor directly.
    _fault_file = open(os.path.join(base_dir, FAULT_FILE_NAME), 'a')
    faulthandler.enable(file=_fault_file, all_threads=True)

    sys.excepthook = _log_uncaught
    threading.excepthook = _log_thread_exception

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:
        # signal handlers can only be installed from the main thread
        pass

    threading.Thread(target=_heartbeat, name='diagnostics-heartbeat', daemon=True).start()

    logger.info('Diagnostics enabled: pid=%d python=%s cwd=%s',
                os.getpid(), sys.version.split()[0], os.getcwd())
