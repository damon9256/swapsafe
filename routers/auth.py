from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, validator
from datetime import datetime
from database import get_db
from security import (verify_telegram_init_data, create_access_token, hash_pin,
                       verify_pin, check_pin_lockout, record_failed_pin,
                       clear_pin_attempts, set_withdrawal_cooldown, get_current_user)
import models
import hashlib

router = APIRouter()

class LoginRequest(BaseModel):
    init_data: str

class SetPinRequest(BaseModel):
    pin: str
    @validator("pin")
    def pin_must_be_6_digits(cls, v):
        if not v.isdigit() or len(v) != 6:
            raise ValueError("PIN must be exactly 6 digits")
        return v

class VerifyPinRequest(BaseModel):
    pin: str
    action: str

class ChangePinRequest(BaseModel):
    old_pin: str
    new_pin: str
    @validator("new_pin")
    def pin_must_be_6_digits(cls, v):
        if not v.isdigit() or len(v) != 6:
            raise ValueError("PIN must be exactly 6 digits")
        return v

@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user_data = verify_telegram_init_data(req.init_data)
    telegram_id = str(user_data.get("id"))
    if not telegram_id:
        raise HTTPException(status_code=400, detail="Could not extract Telegram user ID")
    user = db.query(models.User).filter(models.User.telegram_id == telegram_id).first()
    if not user:
        user = models.User(
            telegram_id=telegram_id,
            username=user_data.get("username"),
            first_name=user_data.get("first_name"),
            last_name=user_data.get("last_name"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        _create_wallets_for_user(user, db)
    else:
        user.username   = user_data.get("username", user.username)
        user.first_name = user_data.get("first_name", user.first_name)
        user.last_name  = user_data.get("last_name", user.last_name)
        user.last_seen  = datetime.utcnow()
        db.commit()
    token = create_access_token(telegram_id)
    return {"token": token, "user": _user_response(user), "has_pin": user.pin_hash is not None}

@router.post("/set-pin")
def set_pin(req: SetPinRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.pin_hash:
        raise HTTPException(status_code=400, detail="PIN already set. Use change-pin.")
    current_user.pin_hash = hash_pin(req.pin)
    db.commit()
    return {"message": "PIN set successfully"}

@router.post("/verify-pin")
def verify_pin_endpoint(req: VerifyPinRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.pin_hash:
        raise HTTPException(status_code=400, detail="PIN not set yet")
    check_pin_lockout(current_user.telegram_id)
    if not verify_pin(req.pin, current_user.pin_hash):
        record_failed_pin(current_user.telegram_id)
        raise HTTPException(status_code=401, detail="Wrong PIN")
    clear_pin_attempts(current_user.telegram_id)
    return {"verified": True, "action": req.action}

@router.post("/change-pin")
def change_pin(req: ChangePinRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.pin_hash:
        raise HTTPException(status_code=400, detail="No PIN set. Use set-pin.")
    check_pin_lockout(current_user.telegram_id)
    if not verify_pin(req.old_pin, current_user.pin_hash):
        record_failed_pin(current_user.telegram_id)
        raise HTTPException(status_code=401, detail="Current PIN is incorrect")
    if verify_pin(req.new_pin, current_user.pin_hash):
        raise HTTPException(status_code=400, detail="New PIN cannot be same as current")
    current_user.pin_hash = hash_pin(req.new_pin)
    db.commit()
    set_withdrawal_cooldown(current_user.telegram_id)
    return {"message": "PIN changed. Withdrawals locked for 24h."}

def _create_wallets_for_user(user: models.User, db: Session):
    for network in models.Network:
        address = _generate_address(network, user.telegram_id)
        wallet = models.Wallet(user_id=user.id, network=network, address=address, balance=0.0, locked=0.0)
        db.add(wallet)
    db.commit()

def _generate_address(network: models.Network, telegram_id: str) -> str:
    seed = f"{network.value}:{telegram_id}:swapsafe"
    h = hashlib.sha256(seed.encode()).hexdigest()
    if network == models.Network.TRC20:
        return f"T{h[:33].upper()}"
    elif network == models.Network.SOLANA:
        return h[:44]
    else:
        return f"0x{h[:40]}"

def _user_response(user: models.User) -> dict:
    return {
        "id": user.id, "telegram_id": user.telegram_id,
        "username": user.username, "first_name": user.first_name,
        "tier": user.tier, "is_frozen": user.is_frozen,
        "total_trades": user.total_trades, "completed_trades": user.completed_trades,
        "completion_rate": user.completion_rate, "avg_rating": user.avg_rating,
        "total_volume": user.total_volume, "created_at": str(user.created_at),
    }
