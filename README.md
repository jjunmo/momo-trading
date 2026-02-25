# jjunmo-fastapi 보일러플레이트

범용 FastAPI 프로젝트 보일러플레이트 템플릿.
Spring Boot 스타일 레이어드 아키텍처 + FastAPI 공식 템플릿의 장점을 결합한 구조로, 새 프로젝트를 시작할 때 이 템플릿을 복사하여 바로 사용할 수 있다.

## 기술 스택

| 기술 | 설명 |
|------|------|
| **FastAPI** | 비동기 웹 프레임워크 |
| **SQLAlchemy 2.0** | ORM (Sync + Async 이중 지원) |
| **Alembic** | DB 마이그레이션 |
| **Pydantic v2** | 데이터 검증 + Settings 관리 |
| **loguru** | 구조화 로깅 (환경별 포맷 분기) |
| **SQLite** | 개발용 DB (PostgreSQL/MySQL 전환 가능) |
| **pytest** | 테스트 프레임워크 |

## 프로젝트 구조

```
fastapi-boilerplate/
├── main.py                          # 앱 진입점
├── requirements.txt
├── .env.example
├── alembic.ini
│
├── core/                            # @Configuration
│   ├── config.py                    # Settings (ENVIRONMENT 분기 포함)
│   ├── database.py                  # Sync + Async Engine, Session, DI generators
│   └── logging.py                   # loguru 설정 (환경별 포맷, intercept handler)
│
├── middleware/                       # 미들웨어
│   └── request_id.py               # X-Request-ID 자동 생성/전파
│
├── models/                          # @Entity
│   └── base.py                      # DeclarativeBase + TimestampMixin
│
├── repositories/                    # @Repository
│   ├── base_repository.py          # Sync Generic[T] CRUD
│   └── async_base_repository.py    # Async Generic[T] CRUD
│
├── services/                        # @Service (도메인별 추가)
│
├── schemas/                         # DTO
│   └── common.py                    # SuccessResponse, BasicErrorResponse
│
├── api/                             # @Controller (라우터 집약)
│   ├── router.py                    # 모든 라우터 등록 (/api/v1 prefix)
│   └── routes/
│       └── health.py                # 헬스체크 예시
│
├── dependencies/                    # @Autowired DI
│   ├── repositories.py             # Repository DI (가이드 포함)
│   └── services.py                 # Service DI (가이드 포함)
│
├── exceptions/                      # @ControllerAdvice
│   ├── error_codes.py              # ErrorCode Enum
│   └── common.py                   # ServiceException Factory
│
├── util/
│   └── time_util.py                # KST 시간 유틸
│
├── alembic/                         # DB 마이그레이션
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│
└── tests/
    ├── conftest.py                  # Sync/Async DB fixture, TestClient
    └── api/
        └── test_health.py           # 헬스체크 테스트 예시
```

## 시작하기

### 1. 환경 설정

```bash
# 가상환경 생성 및 활성화
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 환경 변수 설정
cp .env.example .env
```

### 2. 의존성 설치

```bash
pip install -r requirements.txt
```

### 3. 서버 실행

```bash
uvicorn main:app --reload
```

서버가 시작되면 http://localhost:8000/docs 에서 Swagger UI를 확인할 수 있다.

## 핵심 패턴 설명

### BaseRepository / AsyncBaseRepository 패턴

`Generic[T]` 기반 공통 CRUD 추상화. 도메인별로 Sync/Async 선택 가능.

- **Sync (`BaseRepository`)**: 단순 CRUD, CPU 바운드 작업
- **Async (`AsyncBaseRepository`)**: 외부 API 호출, 동시 다발적 I/O 바운드 작업

```python
# Sync Repository 예시
class TaskRepository(BaseRepository[Task]):
    def __init__(self, db: Session):
        super().__init__(Task, db)

# Async Repository 예시
class TaskAsyncRepository(AsyncBaseRepository[Task]):
    def __init__(self, db: AsyncSession):
        super().__init__(Task, db)
```

Repository에서는 `flush()`만 수행하고, `commit()`은 DI 레이어(`get_db_with_transaction`)가 자동 관리한다.

### Type Alias DI 패턴

읽기용(`get_db`)과 트랜잭션용(`get_db_with_transaction`) 세션을 분리하여 Type Alias로 DI를 구성한다.

```python
# dependencies/repositories.py
TaskRepoDep = Annotated[TaskRepository, Depends(get_task_repository)]
TaskRepoTransactionDep = Annotated[TaskRepository, Depends(get_task_repository_with_transaction)]
```

**네이밍 컨벤션:**

| 구분 | Sync | Async |
|------|------|-------|
| 읽기용 Repo | `{Entity}RepoDep` | `{Entity}AsyncRepoDep` |
| 트랜잭션 Repo | `{Entity}RepoTransactionDep` | `{Entity}AsyncRepoTransactionDep` |
| 읽기용 Service | `{Entity}ServiceDep` | `{Entity}AsyncServiceDep` |
| 트랜잭션 Service | `{Entity}ServiceTransactionDep` | `{Entity}AsyncServiceTransactionDep` |

### ServiceException Factory 패턴

비즈니스 예외를 팩토리 메서드로 생성. 글로벌 예외 핸들러가 JSON 응답으로 변환한다.

```python
raise ServiceException.not_found("카테고리를 찾을 수 없습니다")
raise ServiceException.conflict("이미 존재하는 항목입니다")
```

## 새 기능 추가 절차 (8단계)

`Task`라는 기능을 예시로 설명한다.

### Step 1. Model 정의

`models/` 디렉토리에 엔티티 모델 생성. `TimestampMixin`을 활용한다.

### Step 2. Migration

```bash
alembic revision --autogenerate -m "add task table"
alembic upgrade head
```

### Step 3. Repository 생성

`repositories/` 디렉토리에 `BaseRepository[Task]` (Sync) 또는 `AsyncBaseRepository[Task]` (Async) 상속.

### Step 4. Repository DI 등록

`dependencies/repositories.py`에 팩토리 함수 + Type Alias 추가.

### Step 5. Service 생성

`services/` 디렉토리에 비즈니스 로직 구현. `ServiceException` 팩토리 활용.

### Step 6. Service DI 등록

`dependencies/services.py`에 팩토리 함수 + Type Alias 추가.

### Step 7. Schema 정의

`schemas/` 디렉토리에 `Create`, `Update`, `Response` DTO 생성.

### Step 8. Router 생성

`api/routes/` 디렉토리에 라우터 생성 후 `api/router.py`에 등록.

```python
# api/router.py
from api.routes import health, task

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(task.router)  # 추가
```

## 환경 설정

`.env` 파일로 환경 변수를 관리한다.

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `DATABASE_URL` | DB 연결 문자열 | `sqlite:///./app.db` |
| `APP_NAME` | 앱 이름 | `jjunmo-fastapi` |
| `APP_VERSION` | 앱 버전 | `0.1.0` |
| `ENVIRONMENT` | 환경 (`local`/`staging`/`production`) | `local` |
| `LOG_LEVEL` | 로그 레벨 | `DEBUG` |
| `CORS_ORIGINS` | 허용 Origins (JSON 배열) | `["http://localhost:3000","http://localhost:8000"]` |

### ENVIRONMENT 분기

| 환경 | Swagger | SQL Echo | 로그 포맷 | 로그 레벨 |
|------|---------|----------|-----------|-----------|
| `local` | 활성 | 활성 | 컬러 콘솔 | DEBUG |
| `staging` | 비활성 | 비활성 | JSON | INFO |
| `production` | 비활성 | 비활성 | JSON | INFO |

Async DB URL은 `core/config.py`의 `async_database_url` 프로퍼티가 sync URL에서 자동 생성한다:
- `sqlite:///` → `sqlite+aiosqlite:///`
- `postgresql://` → `postgresql+asyncpg://`
- `mysql://` → `mysql+aiomysql://`

## 로깅

[loguru](https://github.com/Delgan/loguru) 기반 구조화 로깅. `core/logging.py`에서 설정을 초기화한다.

- **InterceptHandler**: uvicorn, SQLAlchemy 등 표준 `logging` 사용 라이브러리의 로그를 loguru로 통합
- **Request ID 자동 포함**: `middleware/request_id.py`에서 `logger.contextualize(request_id=...)`로 모든 로그에 자동 포함

```python
# Service/Repository에서 바로 사용
from loguru import logger

logger.info("작업 완료: {}", task_id)
```

## 테스트

```bash
# 전체 테스트 실행
pytest tests/ -v

# 특정 테스트 실행
pytest tests/api/test_health.py -v
```

테스트는 인메모리 SQLite를 사용하며, `tests/conftest.py`에서 DI override로 테스트 DB 세션을 주입한다.

## API 문서

로컬 환경(`ENVIRONMENT=local`)에서만 활성화된다.

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
