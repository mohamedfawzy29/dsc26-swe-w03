from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ProblemError(Exception):
    def __init__(self, status: int, slug: str, title: str, detail: str, headers=None):
        self.status, self.slug, self.title, self.detail, self.headers = status, slug, title, detail, headers or {}


def problem(status, slug, title, detail, headers=None):
    return JSONResponse({"type": f"urn:rawaj:problem:{slug}", "title": title, "status": status, "detail": detail},
                        status_code=status, media_type="application/problem+json", headers=headers or {})


async def problem_error_handler(request: Request, exc: ProblemError):
    return problem(exc.status, exc.slug, exc.title, exc.detail, exc.headers)


async def validation_error_handler(request: Request, exc: RequestValidationError):
    return problem(422, "invalid-request", "Unprocessable Entity", "Request validation failed")


async def http_error_handler(request: Request, exc: StarletteHTTPException):
    status = exc.status_code
    title = "Not Found" if status == 404 else "Method Not Allowed" if status == 405 else "HTTP Error"
    return problem(status, "not-found" if status == 404 else "method-not-allowed" if status == 405 else "http-error", title, str(exc.detail))
