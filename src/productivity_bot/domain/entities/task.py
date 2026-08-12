from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    title: str
