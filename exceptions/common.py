from fastapi import HTTPException

from exceptions.error_codes import ErrorCode


class ServiceException(HTTPException):
    def __init__(
        self,
        error_code: ErrorCode,
        message: str = None,
        status_code: int = 400,
        data: dict = None,
    ):
        self.error_code = error_code
        self.message = message or error_code.value
        self.data = data
        super().__init__(
            status_code=status_code,
            detail={"code": error_code, "message": self.message},
        )

    # ── Factory 메서드 ──

    @staticmethod
    def not_found(message: str) -> "ServiceException":
        return ServiceException(
            status_code=404, error_code=ErrorCode.NOT_FOUND, message=message
        )

    @staticmethod
    def bad_request(message: str) -> "ServiceException":
        return ServiceException(
            status_code=400, error_code=ErrorCode.BAD_REQUEST, message=message
        )

    @staticmethod
    def unauthorized(message: str) -> "ServiceException":
        return ServiceException(
            status_code=401, error_code=ErrorCode.UNAUTHORIZED, message=message
        )

    @staticmethod
    def forbidden(message: str) -> "ServiceException":
        return ServiceException(
            status_code=403, error_code=ErrorCode.FORBIDDEN, message=message
        )

    @staticmethod
    def conflict(message: str) -> "ServiceException":
        return ServiceException(
            status_code=409, error_code=ErrorCode.ALREADY_EXISTS, message=message
        )

    @staticmethod
    def internal_server_error(message: str) -> "ServiceException":
        return ServiceException(
            status_code=500,
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            message=message,
        )
