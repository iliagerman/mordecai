"""Task API endpoints.

Routers handle HTTP concerns only - no business logic.
All business logic is delegated to TaskService.
"""

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query

from app.security.whitelist import enforce_whitelist_or_403

from app.enums import TaskStatus
from app.models.base import JsonModel
from app.models.domain import Task

if TYPE_CHECKING:
    from app.services.task_service import TaskService


class CreateTaskRequest(JsonModel):
    """Request model for task creation."""

    user_id: str
    title: str
    description: str = ""


class UpdateTaskStatusRequest(JsonModel):
    """Request model for status update."""

    status: TaskStatus


class TaskResponse(JsonModel):
    """Response model for task operations."""

    task_id: str
    status: str


class TaskListResponse(JsonModel):
    """Response model for task list grouped by status."""

    pending: list[Task]
    in_progress: list[Task]
    done: list[Task]


def create_task_router(
    task_service: "TaskService",
    *,
    allowed_users: list[str] | None = None,
) -> APIRouter:
    """Create task router with injected service.

    Args:
        task_service: TaskService instance for business logic

    Returns:
        APIRouter with task endpoints configured
    """
    router = APIRouter(prefix="/api/tasks", tags=["tasks"])

    @router.get("/{user_id}", response_model=TaskListResponse)
    async def get_tasks(user_id: str) -> TaskListResponse:
        """Get all tasks for a user grouped by status.

        Args:
            user_id: The user's ID

        Returns:
            Tasks grouped by status (pending, in_progress, done)
        """
        enforce_whitelist_or_403(user_id, allowed_users)
        grouped = await task_service.get_tasks_grouped_by_status(user_id)
        return TaskListResponse(**grouped)

    @router.post("", response_model=TaskResponse)
    async def create_task(request: CreateTaskRequest) -> TaskResponse:
        """Create a new task.

        Args:
            request: Task creation request with user_id, title, description

        Returns:
            TaskResponse with task_id and status

        Raises:
            HTTPException: 400 if validation fails
        """
        try:
            enforce_whitelist_or_403(request.user_id, allowed_users)
            task = await task_service.create_task(
                user_id=request.user_id,
                title=request.title,
                description=request.description,
            )
            return TaskResponse(task_id=task.id, status="created")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.patch("/{task_id}/status", response_model=TaskResponse)
    async def update_task_status(
        task_id: str,
        request: UpdateTaskStatusRequest,
        user_id: str = Query(..., description="User ID for authorization"),
    ) -> TaskResponse:
        """Update task status.

        Args:
            task_id: The task's ID
            request: Status update request
            user_id: User ID (would come from auth in real app)

        Returns:
            TaskResponse with task_id and status

        Raises:
            HTTPException: 404 if task not found, 403 if permission denied
        """
        try:
            enforce_whitelist_or_403(user_id, allowed_users)
            success = await task_service.update_task_status(
                task_id=task_id,
                status=request.status,
                user_id=user_id,
            )
            if not success:
                raise HTTPException(status_code=404, detail="Task not found")
            return TaskResponse(task_id=task_id, status="updated")
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))

    return router
