"""Explicit parent/child graph constraints; no runtime recursion."""

from __future__ import annotations

from ..domain.protocol import Task


class TaskGraphError(ValueError):
    pass


class TaskGraph:
    def __init__(self, *, max_depth: int = 4, max_children_per_task: int = 8, max_total_tasks_per_root: int = 128) -> None:
        self.max_depth = max_depth
        self.max_children_per_task = max_children_per_task
        self.max_total_tasks_per_root = max_total_tasks_per_root
        self.tasks: dict[str, Task] = {}
        self.children: dict[str, list[str]] = {}

    def add(self, task: Task) -> None:
        if task.task_id in self.tasks:
            raise TaskGraphError(f"duplicate task: {task.task_id}")
        if task.depth > self.max_depth:
            raise TaskGraphError("max depth exceeded")
        if task.parent_task_id:
            parent = self.tasks.get(task.parent_task_id)
            if parent is None:
                raise TaskGraphError("parent task is not present")
            if task.task_id == parent.task_id or task.depth != parent.depth + 1:
                raise TaskGraphError("invalid parent/depth or cycle")
            children = self.children.setdefault(parent.task_id, [])
            if len(children) >= self.max_children_per_task:
                raise TaskGraphError("max child tasks exceeded")
            root = parent.root_task_id or parent.task_id
            if sum(1 for item in self.tasks.values() if (item.root_task_id or item.task_id) == root) >= self.max_total_tasks_per_root:
                raise TaskGraphError("max total tasks per root exceeded")
            children.append(task.task_id)
            task.root_task_id = parent.root_task_id
        self.tasks[task.task_id] = task
        try:
            self.validate()
        except Exception:
            self.tasks.pop(task.task_id, None)
            if task.parent_task_id and task.task_id in self.children.get(task.parent_task_id, []):
                self.children[task.parent_task_id].remove(task.task_id)
            raise

    @classmethod
    def from_tasks(cls, tasks: list[Task], **limits: int) -> "TaskGraph":
        graph = cls(**limits)
        for task in sorted(tasks, key=lambda item: (item.depth, item.created_at, item.task_id)):
            graph.add(task)
        graph.validate()
        return graph

    def add_child(self, parent_task_id: str, task: Task) -> None:
        task.parent_task_id = parent_task_id
        task.depth = self.tasks[parent_task_id].depth + 1 if parent_task_id in self.tasks else task.depth
        self.add(task)

    def descendants(self, task_id: str) -> list[str]:
        result: list[str] = []
        pending = list(self.children.get(task_id, []))
        while pending:
            child = pending.pop(0)
            result.append(child)
            pending.extend(self.children.get(child, []))
        return result

    def validate(self) -> None:
        """Detect cycles even if a caller mutates a Task after insertion."""
        for task_id, task in self.tasks.items():
            visited: set[str] = set()
            current = task_id
            while current:
                if current in visited:
                    raise TaskGraphError("cycle detected")
                visited.add(current)
                parent = self.tasks[current].parent_task_id
                if not parent:
                    break
                if parent not in self.tasks:
                    raise TaskGraphError("parent task is not present")
                current = parent
