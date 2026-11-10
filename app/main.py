import json
import threading
import uuid

from fastapi import Body, Depends, FastAPI, Header, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import current_customer
from app.db import make_engine, make_session_factory
from app.errors import ProblemError, http_error_handler, problem, problem_error_handler, validation_error_handler
from app.models import Account, IdempotencyRecord, Transfer
from app.ratelimit import TokenBucketLimiter
from app.schemas import TransferIn, request_hash, transfer_json
from app.settings import Settings


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.db_lock = threading.Lock()
    app.state.limiter = TokenBucketLimiter(settings.rate_limit_capacity, settings.rate_limit_refill_per_second, settings.clock)
    app.add_exception_handler(ProblemError, problem_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)

    def rate_limited_customer(customer: str = Depends(current_customer)) -> str:
        allowed, retry = app.state.limiter.consume(customer)
        if not allowed:
            raise ProblemError(429, "rate-limited", "Too Many Requests", "Transfer rate limit exceeded", {"Retry-After": str(retry)})
        return customer

    @app.post("/v1/transfers")
    def post_transfer(payload: object = Body(default=None), idempotency_key: str | None = Header(default=None),
                      customer: str = Depends(rate_limited_customer)):
        if idempotency_key is None or idempotency_key == "":
            return problem(400, "missing-idempotency-key", "Bad Request", "Idempotency-Key header is required")
        try:
            request = TransferIn.model_validate(payload)
            if request.amount_piastres <= 0 or request.amount_piastres > settings.max_transfer_piastres:
                raise ValueError("amount outside limit")
            if request.from_account_id == request.to_account_id:
                raise ValueError("source and destination must differ")
        except (ValidationError, ValueError, TypeError):
            return problem(422, "invalid-transfer", "Unprocessable Entity", "Transfer body is invalid")

        digest = request_hash(request)
        with app.state.db_lock:
            try:
                with app.state.session_factory() as session, session.begin():
                    existing = session.scalar(select(IdempotencyRecord).where(
                        IdempotencyRecord.customer_id == customer, IdempotencyRecord.key == idempotency_key))
                    if existing:
                        if existing.request_hash != digest:
                            return problem(422, "idempotency-conflict", "Unprocessable Entity", "Idempotency-Key was used with a different request")
                        media = "application/json" if existing.status_code == 201 else "application/problem+json"
                        return Response(content=existing.response_body, status_code=existing.status_code,
                                        media_type=media, headers={"Idempotent-Replayed": "true"})
                    source = session.get(Account, request.from_account_id)
                    if source is None or source.owner_id != customer:
                        return problem(404, "not-found", "Not Found", "Source account not found")
                    destination = session.get(Account, request.to_account_id)
                    if destination is None:
                        return problem(422, "invalid-destination", "Unprocessable Entity", "Destination account not found")
                    if source.balance_piastres < request.amount_piastres:
                        body = json.dumps({"type": "urn:rawaj:problem:insufficient-funds", "title": "Conflict", "status": 409,
                                           "detail": "Insufficient funds"}, separators=(",", ":"))
                        session.add(IdempotencyRecord(customer_id=customer, key=idempotency_key, request_hash=digest,
                                                      status_code=409, response_body=body))
                        session.flush()
                        return Response(content=body, status_code=409, media_type="application/problem+json")
                    source.balance_piastres -= request.amount_piastres
                    destination.balance_piastres += request.amount_piastres
                    transfer = Transfer(id="trf_" + uuid.uuid4().hex, from_account_id=request.from_account_id,
                                        to_account_id=request.to_account_id, amount_piastres=request.amount_piastres,
                                        memo=request.memo, status="completed")
                    session.add(transfer)
                    body = json.dumps(transfer_json(transfer), separators=(",", ":"))
                    session.add(IdempotencyRecord(customer_id=customer, key=idempotency_key, request_hash=digest,
                                                  status_code=201, response_body=body))
                    session.flush()
                    return Response(content=body, status_code=201, media_type="application/json")
            except IntegrityError:
                with app.state.session_factory() as session:
                    existing = session.scalar(select(IdempotencyRecord).where(
                        IdempotencyRecord.customer_id == customer, IdempotencyRecord.key == idempotency_key))
                    if existing and existing.request_hash == digest:
                        media = "application/json" if existing.status_code == 201 else "application/problem+json"
                        return Response(content=existing.response_body, status_code=existing.status_code, media_type=media,
                                        headers={"Idempotent-Replayed": "true"})
                    if existing:
                        return problem(422, "idempotency-conflict", "Unprocessable Entity", "Idempotency-Key was used with a different request")
                    raise

    @app.get("/v1/accounts/{account_id}")
    def get_account(account_id: str, customer: str = Depends(current_customer)):
        with app.state.db_lock, Session(app.state.engine) as session:
            account = session.get(Account, account_id)
            if account is None or account.owner_id != customer:
                return problem(404, "not-found", "Not Found", "Account not found")
            return {"id": account.id, "balance_piastres": account.balance_piastres, "currency": account.currency}

    @app.get("/v1/transfers/{transfer_id}")
    def get_transfer(transfer_id: str, customer: str = Depends(current_customer)):
        with app.state.db_lock, Session(app.state.engine) as session:
            transfer = session.get(Transfer, transfer_id)
            if transfer is None:
                return problem(404, "not-found", "Not Found", "Transfer not found")
            source, destination = session.get(Account, transfer.from_account_id), session.get(Account, transfer.to_account_id)
            if not ((source and source.owner_id == customer) or (destination and destination.owner_id == customer)):
                return problem(404, "not-found", "Not Found", "Transfer not found")
            return transfer_json(transfer)

    return app
