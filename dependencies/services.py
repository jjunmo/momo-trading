"""
Service DI 등록
================
도메인별 Service를 추가할 때 아래 패턴을 따른다.

Sync 예시:
    from typing import Annotated
    from fastapi import Depends
    from dependencies.repositories import TaskRepoDep, TaskRepoTransactionDep
    from services.task_service import TaskService

    def get_task_service_read_only(task_repo: TaskRepoDep) -> TaskService:
        return TaskService(task_repo=task_repo)

    def get_task_service_transactional(task_repo: TaskRepoTransactionDep) -> TaskService:
        return TaskService(task_repo=task_repo)

    TaskServiceDep = Annotated[TaskService, Depends(get_task_service_read_only)]
    TaskServiceTransactionDep = Annotated[TaskService, Depends(get_task_service_transactional)]

Async 예시:
    from typing import Annotated
    from fastapi import Depends
    from dependencies.repositories import TaskAsyncRepoDep, TaskAsyncRepoTransactionDep
    from services.task_async_service import TaskAsyncService

    def get_task_async_service_read_only(
        task_repo: TaskAsyncRepoDep,
    ) -> TaskAsyncService:
        return TaskAsyncService(task_repo=task_repo)

    def get_task_async_service_transactional(
        task_repo: TaskAsyncRepoTransactionDep,
    ) -> TaskAsyncService:
        return TaskAsyncService(task_repo=task_repo)

    TaskAsyncServiceDep = Annotated[TaskAsyncService, Depends(get_task_async_service_read_only)]
    TaskAsyncServiceTransactionDep = Annotated[
        TaskAsyncService, Depends(get_task_async_service_transactional)
    ]
"""
