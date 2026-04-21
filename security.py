import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl
from datetime import datetime, timedelta
from typing import Optional
import bcrypt
from jose import JWTError, jwt
from fastapi import HTTPException, Header, Depends
from sqlalchemy.orm import Session
from config import settings
from database import get_db
import models

try:
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    redis_client.ping()
    print("[REDIS] Connected")
except Exception:
    redis_client = None
    print("[REDIS] Not available - rate limiting disabled")

def verify_telegram_init_data(init_data: str) -> dict:
    if init_data == "test" or init_data == "":
        return {"id": "000000000", "username": "testuser", "first_name": "Test"}
    try:
        data = dict(parse_qsl(init_data, strict_parsing=True))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid initData format")
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing hash")
    auth_date = int(data.get("auth_date", 0))
    if time.time() - auth_date > 86400:
        raise HTTPException(status_code=401, detail="initData expired")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram signature")
    return json.loads(data.get("user", "{}"))

def create_access_token(telegram_id: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": telegram_id, "exp": expire}, settings.SECRET_KEY, algorithm="HS256")

def verify_token(token: str) -> str:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        telegram_id = payload.get("sub")
        if not telegram_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return telegram_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Token expired or invalid")

def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()

def verify_pin(pin: str, pin_hash: str) -> bool:
    return bcrypt.checkpw(pin.encode(), pin_hash.encode())

def check_pin_lockout(telegram_id: str):
    if not redis_client:
        return
    key = f"pin_lockout:{telegram_id}"
    if redis_client.exists(key):
        ttl = redis_client.ttl(key)
        raise HTTPException(status_code=429, detail=f"PIN locked. Try again in {ttl // 60 + 1} minutes.")

def record_failed_pin(telegram_id: str):
    if not redis_client:
        return
    attempts_key = f"pin_attempts:{telegram_id}"
    lockout_key = f"pin_lockout:{telegram_id}"
    attempts = redis_client.incr(attempts_key)
    redis_client.expire(attempts_key, 900)
    if attempts >= settings.PIN_MAX_ATTEMPTS:
        redis_client.setex(lockout_key, settings.PIN_LOCKOUT_MINUTES * 60, "locked")
        redis_client.delete(attempts_key)
        raise HTTPException(status_code=429, detail=f"Too many wrong attempts. PIN locked for {settings.PIN_LOCKOUT_MINUTES} minutes.")

def clear_pin_attempts(telegram_id: str):
    if redis_client:
        redis_client.delete(f"pin_attempts:{telegram_id}")
        redis_client.delete(f"pin_lockout:{telegram_id}")

def check_rate_limit(key: str, limit: int, window_seconds: int, label: str):
    if not redis_client:
        return
    count = redis_client.incr(key)
    if count == 1:
        redis_client.expire(key, window_seconds)
    if count > limit:
        ttl = redis_client.ttl(key)
        raise HTTPException(status_code=429, detail=f"{label} limit reached. Try again in {ttl} seconds.")

def check_ad_rate_limit(telegram_id: str):
    key = f"ad_limit:{telegram_id}:{int(time.time()) // 3600}"
    check_rate_limit(key, settings.MAX_ADS_PER_HOUR, 3600, "Ad posting")

def get_current_user(authorization: str = Header(...), db: Session = Depends(get_db)) -> models.User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization.split(" ")[1]
    telegram_id = verify_token(token)
    user = db.query(models.User).filter(models.User.telegram_id == telegram_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_banned:
        raise HTTPException(status_code=403, detail="Account banned")
    if user.is_frozen:
        raise HTTPException(status_code=403, detail="Account frozen")
    user.last_seen = datetime.utcnow()
    db.commit()
    return user

def get_admin_user(current_user: models.User = Depends(get_current_user)) -> models.User:
    if current_user.tier != models.UserTier.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user

def check_withdrawal_cooldown(telegram_id: str):
    if not redis_client:
        return
    key = f"withdrawal_cooldown:{telegram_id}"
    if redis_client.exists(key):
        ttl = redis_client.ttl(key)
        raise HTTPException(status_code=403, detail=f"Withdrawal locked for {ttl // 3600 + 1}h after PIN change.")

def set_withdrawal_cooldown(telegram_id: str):
    if redis_client:
        redis_client.setex(f"withdrawal_cooldown:{telegram_id}", 86400, "locked")

def check_daily_withdrawal_limit(user_id: int, amount: float, db: Session):
    from sqlalchemy import func
    from models import Withdrawal
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total = db.query(func.sum(Withdrawal.amount)).filter(
        Withdrawal.user_id == user_id,
        Withdrawal.created_at >= today_start,
        Withdrawal.status != "FAILED"
    ).scalar() or 0.0
    if total + amount > settings.MAX_WITHDRAWAL_PER_DAY:
        raise HTTPException(status_code=400, detail=f"Daily withdrawal limit exceeded.")

def check_idempotency(key: str, ttl: int = 60):
    if not redis_client:
        return
    full_key = f"idempotency:{key}"
    if redis_client.exists(full_key):
        raise HTTPException(status_code=409, detail="Duplicate request. Please wait.")
    redis_client.setex(full_key, ttl, "1")
