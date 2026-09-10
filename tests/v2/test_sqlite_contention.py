import multiprocessing
from concurrent.futures import ThreadPoolExecutor

from src.dev_agent._sqlite import BUSY_TIMEOUT_MS
from src.dev_agent.domain.protocol import Task
from src.dev_agent.operation import OperationControl
from src.dev_agent.scheduler.queue import DurableQueue
from src.dev_agent.state.sqlite_store import SQLiteStateStore


def _process_toggle_stop(path: str, result_queue) -> None:
    control = OperationControl(path)
    try:
        for _ in range(8):
            control.request_stop()
            control.clear_stop()
        result_queue.put(("control", "ok", control.stop_requested()))
    except BaseException as exc:
        result_queue.put(("control", type(exc).__name__, str(exc)))
    finally:
        control.close()


def _process_write_queue(path: str, result_queue) -> None:
    queue = DurableQueue(path)
    try:
        for index in range(8):
            task_id = f"contention-queue-{index}"
            queue.enqueue(task_id)
            queue.snapshot(task_id)
            queue.cancel(task_id)
        result_queue.put(("queue", "ok", 8))
    except BaseException as exc:
        result_queue.put(("queue", type(exc).__name__, str(exc)))
    finally:
        queue.close()


def _process_write_state(path: str, result_queue) -> None:
    from src.dev_agent.state.sqlite_store import SQLiteStateStore

    with SQLiteStateStore(path) as store:
        try:
            for index in range(8):
                task = Task(objective=f"contention state {index}")
                store.save_task(task)
                assert store.load_task(task.task_id) is not None
            result_queue.put(("state", "ok", 8))
        except BaseException as exc:
            result_queue.put(("state", type(exc).__name__, str(exc)))


def _process_read_status(path: str, result_queue) -> None:
    from src.dev_agent.scheduler.queue import DurableQueue
    from src.dev_agent.state.sqlite_store import SQLiteStateStore

    with SQLiteStateStore(path) as store, DurableQueue(path) as queue:
        try:
            for _ in range(8):
                store.snapshot()
                queue.connection.execute("SELECT COUNT(*) FROM queue_items").fetchone()
            result_queue.put(("status", "ok", 8))
        except BaseException as exc:
            result_queue.put(("status", type(exc).__name__, str(exc)))


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


def test_durable_state_queue_stop_and_status_survive_process_contention(tmp_path):
    path = tmp_path / "process-contention.sqlite3"
    # Initialize all durable schemas before workers open independent
    # connections.  The workers then exercise the same WAL database from
    # separate processes, which is the operational shape of start/status/stop.
    with SQLiteStateStore(path) as store:
        store.save_task(Task(objective="contention seed"))
    with DurableQueue(path):
        pass
    control = OperationControl(path)
    control.close()

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    processes = [
        context.Process(target=_process_toggle_stop, args=(str(path), result_queue)),
        context.Process(target=_process_write_queue, args=(str(path), result_queue)),
        context.Process(target=_process_write_state, args=(str(path), result_queue)),
        context.Process(target=_process_read_status, args=(str(path), result_queue)),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
    for process in processes:
        if process.is_alive():
            process.terminate()
        assert process.exitcode == 0

    results = [result_queue.get(timeout=5) for _ in processes]
    assert {item[0] for item in results} == {"control", "queue", "state", "status"}
    assert all(item[1] == "ok" for item in results), results
