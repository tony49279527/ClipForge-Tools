"""
RQ Worker entrypoint for ClipForge Tools.

Run with:
    python worker.py                  # default: 3 workers
    python worker.py --workers 5      # custom worker count
    python worker.py --burst          # process all queued jobs then exit
"""

import os
import sys
import argparse
import multiprocessing as mp

from rq import Worker
from task_queue import get_redis, QUEUE_NAME


def run_worker_process(worker_index: int, burst: bool) -> None:
    """Run one real RQ worker process."""
    redis_conn = get_redis()
    worker = Worker([QUEUE_NAME], connection=redis_conn, name=f"clipforge-w{worker_index}")
    worker.work(burst=burst)


def main():
    parser = argparse.ArgumentParser(description="ClipForge RQ Worker")
    parser.add_argument("--workers", type=int, default=int(os.getenv("RQ_WORKER_COUNT", "3")),
                        help="Number of worker processes (default: 3)")
    parser.add_argument("--burst", action="store_true",
                        help="Process all queued jobs then exit")
    args = parser.parse_args()

    redis_conn = get_redis()
    redis_conn.ping()

    print(f"🚀 Starting ClipForge RQ Worker(s): {args.workers} processes, queue={QUEUE_NAME}")
    print(f"   Redis: {os.getenv('REDIS_URL', 'redis://localhost:6379/0')}")
    print(f"   Burst mode: {args.burst}")
    print()

    processes: list[mp.Process] = []
    exit_code = 0

    try:
        for worker_index in range(1, args.workers + 1):
            process = mp.Process(
                target=run_worker_process,
                args=(worker_index, args.burst),
                daemon=False,
            )
            process.start()
            processes.append(process)

        for process in processes:
            process.join()
            if process.exitcode not in (0, None) and exit_code == 0:
                exit_code = process.exitcode
    except KeyboardInterrupt:
        print("\nStopping workers...")
        exit_code = 130
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            if process.pid is not None:
                process.join(timeout=5)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
