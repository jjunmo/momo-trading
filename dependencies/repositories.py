"""
Repository DI 등록
==================
도메인별 Repository를 추가할 때 아래 패턴을 따른다.

Sync 예시:
    from sqlalchemy.orm import Session
    from fastapi import Depends
    from typing import Annotated
    from core.database import get_db, get_db_with_transaction
    from repositories.task_repository import TaskRepository

    def get_task_repository(db: Session = Depends(get_db)) -> TaskRepository:
        return TaskRepository(db)

    def get_task_repository_with_transaction(
        db: Session = Depends(get_db_with_transaction),
    ) -> TaskRepository:
        return TaskRepository(db)

    TaskRepoDep = Annotated[TaskRepository, Depends(get_task_repository)]
    TaskRepoTransactionDep = Annotated[TaskRepository, Depends(get_task_repository_with_transaction)]

Async 예시:
    from sqlalchemy.ext.asyncio import AsyncSession
    from fastapi import Depends
    from typing import Annotated
    from core.database import get_async_db, get_async_db_with_transaction
    from repositories.task_async_repository import TaskAsyncRepository

    async def get_task_async_repository(
        db: AsyncSession = Depends(get_async_db),
    ) -> TaskAsyncRepository:
        return TaskAsyncRepository(db)

    async def get_task_async_repository_with_transaction(
        db: AsyncSession = Depends(get_async_db_with_transaction),
    ) -> TaskAsyncRepository:
        return TaskAsyncRepository(db)

    TaskAsyncRepoDep = Annotated[TaskAsyncRepository, Depends(get_task_async_repository)]
    TaskAsyncRepoTransactionDep = Annotated[
        TaskAsyncRepository, Depends(get_task_async_repository_with_transaction)
    ]
"""
