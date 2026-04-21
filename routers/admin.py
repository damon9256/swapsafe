from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from database import get_db
from security import get_admin_user
import models
from bot import send_notification

router = APIRouter()

class AssignTierRequest(BaseModel):
    tier: models.UserTier

class FreezeRequest(BaseModel):
    reason: Optional[str] = None

class DisputeResolveRequest(BaseModel):
    outcome: models.DisputeOutcome
    admin_notes: Optional[str] = None

class FeeUpdateRequest(BaseModel):
    platform_fee_percent: float
    withdrawal_fee_usdt: float

class BroadcastRequest(BaseModel):
    message: str

class ManualCreditRequest(BaseModel):
    user_id: int
    network: models.Network
    amount: float
    reason: str

@router.get("/dashboard")
def dashboard(admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    return {
        "total_users": db.query(func.count(models.User.id)).scalar(),
        "active_trades": db.query(func.count(models.Trade.id)).filter(models.Trade.status.in_([models.TradeStatus.PENDING, models.TradeStatus.PAID, models.TradeStatus.CONFIRMING])).scalar(),
        "open_disputes": db.query(func.count(models.Dispute.id)).filter(models.Dispute.outcome == models.DisputeOutcome.PENDING).scalar(),
        "total_volume_usdt": round(db.query(func.sum(models.Trade.usdt_amount)).filter(models.Trade.status == models.TradeStatus.COMPLETED).scalar() or 0.0, 2),
        "completed_trades": db.query(func.count(models.Trade.id)).filter(models.Trade.status == models.TradeStatus.COMPLETED).scalar(),
        "total_fees_usdt": round(db.query(func.sum(models.Trade.platform_fee)).filter(models.Trade.status == models.TradeStatus.COMPLETED).scalar() or 0.0, 6),
    }

@router.get("/users")
def list_users(search: Optional[str] = None, page: int = 1, limit: int = 50, admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    query = db.query(models.User)
    if search:
        query = query.filter((models.User.username.ilike(f"%{search}%")) | (models.User.telegram_id == search))
    users = query.order_by(models.User.created_at.desc()).offset((page-1)*limit).limit(limit).all()
    return [{"id": u.id, "telegram_id": u.telegram_id, "username": u.username, "first_name": u.first_name, "tier": u.tier, "is_frozen": u.is_frozen, "is_banned": u.is_banned, "total_trades": u.total_trades, "completion_rate": u.completion_rate, "created_at": str(u.created_at)} for u in users]

@router.post("/users/{user_id}/assign-tier")
def assign_tier(user_id: int, req: AssignTierRequest, admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.tier = req.tier
    _log(db, admin.id, "ASSIGN_TIER", user_id, str(req.tier))
    db.commit()
    send_notification(user.telegram_id, f"🏅 Your account tier has been updated to {req.tier.value}!")
    return {"message": f"Tier updated to {req.tier}"}

@router.post("/users/{user_id}/freeze")
def freeze_user(user_id: int, req: FreezeRequest, admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_frozen = True
    db.query(models.Ad).filter(models.Ad.user_id == user_id, models.Ad.is_active == True).update({"is_paused": True})
    _log(db, admin.id, "FREEZE_USER", user_id, req.reason)
    db.commit()
    return {"message": "User frozen"}

@router.post("/users/{user_id}/unfreeze")
def unfreeze_user(user_id: int, admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_frozen = False
    _log(db, admin.id, "UNFREEZE_USER", user_id)
    db.commit()
    return {"message": "User unfrozen"}

@router.post("/users/{user_id}/ban")
def ban_user(user_id: int, req: FreezeRequest, admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_banned = True
    user.is_frozen = True
    _log(db, admin.id, "BAN_USER", user_id, req.reason)
    db.commit()
    return {"message": "User banned"}

@router.get("/disputes")
def list_disputes(admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    disputes = db.query(models.Dispute).order_by(models.Dispute.created_at.desc()).limit(100).all()
    return [{"id": d.id, "trade_ref": d.trade.trade_ref, "raised_by": d.raised_by.username, "reason": d.reason, "outcome": d.outcome, "created_at": str(d.created_at)} for d in disputes]

@router.post("/disputes/{dispute_id}/resolve")
def resolve_dispute(dispute_id: int, req: DisputeResolveRequest, admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    dispute = db.query(models.Dispute).filter(models.Dispute.id == dispute_id).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    if dispute.outcome != models.DisputeOutcome.PENDING:
        raise HTTPException(status_code=400, detail="Already resolved")
    trade = dispute.trade
    dispute.outcome = req.outcome
    dispute.admin_notes = req.admin_notes
    dispute.resolved_by = admin.id
    dispute.resolved_at = datetime.utcnow()
    trade.status = models.TradeStatus.RESOLVED
    seller_wallet = db.query(models.Wallet).filter(models.Wallet.user_id == trade.seller_id, models.Wallet.network == trade.network).first()
    buyer_wallet = db.query(models.Wallet).filter(models.Wallet.user_id == trade.buyer_id, models.Wallet.network == trade.network).first()
    if req.outcome == models.DisputeOutcome.BUYER_WON:
        buyer_wallet.balance += trade.usdt_amount
        seller_wallet.locked -= trade.usdt_amount
        send_notification(trade.buyer.telegram_id, f"⚖️ Dispute resolved in your favour. {trade.usdt_amount} USDT credited.")
        send_notification(trade.seller.telegram_id, f"⚖️ Dispute resolved. Decision: Buyer won.")
    elif req.outcome == models.DisputeOutcome.SELLER_WON:
        seller_wallet.balance += trade.usdt_amount
        seller_wallet.locked -= trade.usdt_amount
        send_notification(trade.seller.telegram_id, f"⚖️ Dispute resolved in your favour. {trade.usdt_amount} USDT returned.")
        send_notification(trade.buyer.telegram_id, f"⚖️ Dispute resolved. Decision: Seller won.")
    _log(db, admin.id, "RESOLVE_DISPUTE", dispute_id, str(req.outcome))
    db.commit()
    return {"message": f"Dispute resolved: {req.outcome}"}

@router.post("/fees/update")
def update_fees(req: FeeUpdateRequest, admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    from config import settings
    settings.PLATFORM_FEE_PERCENT = req.platform_fee_percent
    settings.WITHDRAWAL_FEE_USDT = req.withdrawal_fee_usdt
    _log(db, admin.id, "UPDATE_FEES", None, f"Platform: {req.platform_fee_percent}%")
    db.commit()
    return {"message": "Fees updated"}

@router.post("/broadcast")
def broadcast(req: BroadcastRequest, admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    users = db.query(models.User).filter(models.User.is_banned == False).all()
    count = sum(1 for u in users if send_notification(u.telegram_id, f"📢 SwapSafe:\n\n{req.message}"))
    _log(db, admin.id, "BROADCAST", None, req.message[:100])
    return {"message": f"Sent to {count} users"}

@router.post("/ads/{ad_id}/feature")
def feature_ad(ad_id: int, admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    ad = db.query(models.Ad).filter(models.Ad.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    ad.is_featured = not ad.is_featured
    db.commit()
    return {"featured": ad.is_featured}

@router.post("/manual-credit")
def manual_credit(req: ManualCreditRequest, admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    wallet = db.query(models.Wallet).filter(models.Wallet.user_id == req.user_id, models.Wallet.network == req.network).first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    wallet.balance += req.amount
    _log(db, admin.id, "MANUAL_CREDIT", req.user_id, f"+{req.amount} USDT {req.network} — {req.reason}")
    db.commit()
    return {"message": f"Credited {req.amount} USDT"}

@router.get("/logs")
def get_logs(page: int = 1, limit: int = 100, admin: models.User = Depends(get_admin_user), db: Session = Depends(get_db)):
    logs = db.query(models.AdminLog).order_by(models.AdminLog.created_at.desc()).offset((page-1)*limit).limit(limit).all()
    return [{"id": l.id, "action": l.action, "target_id": l.target_id, "details": l.details, "admin_id": l.admin_id, "created_at": str(l.created_at)} for l in logs]

def _log(db, admin_id, action, target_id=None, details=None):
    db.add(models.AdminLog(admin_id=admin_id, action=action, target_id=target_id, details=details))
