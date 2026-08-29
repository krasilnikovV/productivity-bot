from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class TaskPriority(Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    title: str
    start: datetime | None = None
    deadline: datetime | None = None
    priority: TaskPriority = TaskPriority.NORMAL
