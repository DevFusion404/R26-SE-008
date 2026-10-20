"""
DIWO GitHub job worker
======================
R26-SE-008 | Bandara S M Y M | IT22277886

Processes the GitHub webhook jobs the web process queued in
github_analysis_jobs. Run it as its own process, next to the web server:

    python github_worker.py           poll forever
    python github_worker.py --once    one pass, then exit (cron / scheduled job)

On Azure Container Apps this is the same image with a different command, e.g. a
second container in the app, or a Container Apps Job running `--once` on a
schedule. It must use the same database as the web process (Supabase in
production — a SQLite file is not shared between containers).

Stopping it is safe at any moment: a job it had claimed is reclaimed by the
next worker once GITHUB_JOB_LOCK_TIMEOUT_SECONDS has passed.
"""

import argparse
import logging
import signal
import threading

import config
from app import create_app
from services.github_job_service import run_worker


def main():
    parser = argparse.ArgumentParser(description="DIWO GitHub job worker")
    parser.add_argument("--once", action="store_true", help="process due jobs once and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not config.github_integration_enabled():
        logging.warning("GITHUB_INTEGRATION_ENABLED is false - the worker will stay idle.")

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    run_worker(create_app(start_github_worker=False), stop_event=stop, once=args.once)


if __name__ == "__main__":
    main()
