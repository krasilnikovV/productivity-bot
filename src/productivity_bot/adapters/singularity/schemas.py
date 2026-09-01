from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import PydanticCustomError


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
    start: datetime | None = None
    deadline: datetime | None = None
    priority: Literal[0, 1, 2] = 1

    @field_validator("start", "deadline", mode="before")
    @classmethod
    def parse_date(cls, value: object) -> datetime | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise PydanticCustomError(
                "date_type",
                "Date must be an ISO-formatted string",
            )
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("Date must be an ISO-formatted string") from error
        if parsed.tzinfo is None:
            return parsed
        return parsed.astimezone(UTC)

    @field_validator("priority", mode="before")
    @classmethod
    def validate_priority_type(cls, value: object) -> int:
        if type(value) is not int:
            raise ValueError("Priority must be an integer")
        return value


class TaskListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    tasks: list[TaskResponse]
