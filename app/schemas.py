import hashlib
import json
from pydantic import BaseModel, ConfigDict, StrictInt, field_validator


class TransferIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_account_id: str
    to_account_id: str
    amount_piastres: StrictInt
    memo: str | None = None

    @field_validator("memo")
    @classmethod
    def memo_length(cls, value):
        if value is not None and len(value) > 140:
            raise ValueError("memo must be at most 140 characters")
        return value


def request_hash(payload: TransferIn) -> str:
    body = {"from_account_id": payload.from_account_id, "to_account_id": payload.to_account_id,
            "amount_piastres": payload.amount_piastres, "memo": payload.memo}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def transfer_json(transfer):
    return {"id": transfer.id, "from_account_id": transfer.from_account_id,
            "to_account_id": transfer.to_account_id, "amount_piastres": transfer.amount_piastres,
            "memo": transfer.memo, "status": transfer.status}
