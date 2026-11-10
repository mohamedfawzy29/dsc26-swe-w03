from fastapi import Request
from app.errors import ProblemError


def current_customer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, sep, token = header.partition(" ")
    if scheme.lower() != "bearer" or not sep or not token.strip() or token.strip() not in request.app.state.settings.api_tokens:
        raise ProblemError(401, "unauthorized", "Unauthorized", "A valid bearer token is required", {"WWW-Authenticate": "Bearer"})
    return request.app.state.settings.api_tokens[token.strip()]
