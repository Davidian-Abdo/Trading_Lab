"""
bots/worker.py

The ONLY entrypoint. Which asset class this process runs is set by the
ASSET env var (crypto|forex|stocks). docker-compose starts three of these.
"""
from dotenv import load_dotenv
load_dotenv('.env.shared')

from core.config import load_worker_config
from core.worker import run_worker


if __name__ == "__main__":
    run_worker(load_worker_config())
