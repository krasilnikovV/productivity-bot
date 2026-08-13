from typing import Literal

from pydantic import BaseModel, ConfigDict


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    title: str


class CompleteTaskRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    checked: Literal[1] = 1


class TaskResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    id: str
    title: str


class TaskListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    tasks: list[TaskResponse]
