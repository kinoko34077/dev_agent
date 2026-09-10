from concurrent.futures import ThreadPoolExecutor

from src.dev_agent._sqlite import BUSY_TIMEOUT_MS
from src.dev_agent.operation import OperationControl
from src.dev_agent.scheduler.queue import DurableQueue
from src.dev_agent.state.sqlite_store import SQLiteStateStore


def test_durable_connections_use_wal_and_bounded_busy_timeout(tmp_path):
    path = tmp_path / "durable.sqlite3"
    with SQLiteStateStore(path) as store:
        assert store.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert store.connection.execute("PRAGMA busy_timeout").fetchone()[0] >= BUSY_TIMEOUT_MS
    with DurableQueue(path) as queue:
        assert queue.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert queue.connection.execute("PRAGMA busy_timeout").fetchone()[0] >= BUSY_TIMEOUT_MS


def test_operation_control_and_queue_can_read_write_concurrently(tmp_path):
    path = tmp_path / "operation.sqlite3"
    control = OperationControl(path)
    queue = DurableQueue(path)
    try:
        def control_round(_index):
            control.request_stop()
            control.clear_stop()
            return control.stop_requested()

        def queue_round(index):
            task_id = f"00000000-0000-0000-0000-{index:012d}"
            queue.enqueue(task_id)
            item = queue.snapshot(task_id)
            queue.cancel(task_id)
            return item.task_id

        with ThreadPoolExecutor(max_workers=4) as workers:
            control_results = list(workers.map(control_round, range(8)))
            queue_results = list(workers.map(queue_round, range(8)))
        assert not any(control_results)
        assert len(queue_results) == 8
    finally:
        queue.close()
        control.close()
