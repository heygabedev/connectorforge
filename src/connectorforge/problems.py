"""Stable, sanitized HTTP problem responses."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class Problem(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


def problem_response(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        {
            "type": f"urn:connectorforge:{code}",
            "title": code.replace("_", " "),
            "status": status,
            "code": code,
            "detail": detail,
        },
        status_code=status,
        media_type="application/problem+json",
    )


async def handle_problem(_: Request, error: Problem) -> JSONResponse:
    return problem_response(error.status, error.code, error.detail)


async def handle_validation(_: Request, error: RequestValidationError) -> JSONResponse:
    # Pydantic's default error payload includes raw input, which may contain credentials.
    return problem_response(
        422, "invalid_request", "The request does not match the endpoint schema."
    )
