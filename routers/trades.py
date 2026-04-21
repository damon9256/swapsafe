from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from pydantic import BaseModel, validator
from datetime import datetime, timedelta
from typing import Optional
import random, string, os, json
from database import get_db
from security import get_current_user, check_idempotency
from config import settings
import models
from bot import send_notification

router = APIRouter()
UPLOAD_DIR = "uploads/proofs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

class InitiateTradeRequest(BaseModel):
    ad_id: int
    usdt_amount: float
    payment_method: models.PaymentMethod
    @validator("usdt_amount")
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be positive")
        return round(v, 6)

class SendMessageRequest(BaseModel):
    content: str

class ReviewRequest(BaseModel):
    rating: int
    comment: Optional[str] = None
    @validator("rating")
    def valid_rating(cls, v):
        if v < 1 or v > 5:
            raise ValueError("Rating must be 1-5")
        return v

class DisputeRequest(BaseModel):
    reason: str
    notes: Optional[str] = None

@router.post("/initiate")
def initiate_trade(req: InitiateTradeRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    check_idempotency(f"trade_init:{current_user.id}:{req.ad_id}", ttl=10)
    ad = db.query(models.Ad).filter(models.Ad.id == req.ad_id, models.Ad.is_active == True, models.Ad.is_paused == False).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found or unavailable")
    if ad.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot trade with your own ad")
    inr_amount = round(req.usdt_amount * ad.rate, 2)
    if inr_amount < ad.min_amount_inr or inr_amount > ad.max_amount_inr:
        raise HTTPException(status_code=400, detail=f"Amount must be between ₹{ad.min_amount_inr} and ₹{ad.max_amount_inr}")
    if req.usdt_amount > ad.available_usdt:
        raise HTTPException(status_code=400, detail="Insufficient USDT in this ad")
    allowed_methods = json.loads(ad.payment_methods)
    if req.payment_method.value not in allowed_methods:
        raise HTTPException(status_code=400, detail="Payment method not supported")
    if ad.ad_type == models.AdType.SELL:
        seller_id = ad.user_id
        buyer_id = current_user.id
        seller_wallet = db.query(models.Wallet).filter(models.Wallet.user_id == seller_id, models.Wallet.network == ad.network).first()
        if not seller_wallet or seller_wallet.balance < req.usdt_amount:
            raise HTTPException(status_code=400, detail="Seller has insufficient balance")
        seller_wallet.balance -= req.usdt_amount
        seller_wallet.locked += req.usdt_amount
    else:
        seller_id = current_user.id
        buyer_id = ad.user_id
        seller_wallet = db.query(models.Wallet).filter(models.Wallet.user_id == current_user.id, models.Wallet.network == ad.network).first()
        if not seller_wallet or seller_wallet.balance < req.usdt_amount:
            raise HTTPException(status_code=400, detail="Insufficient USDT to sell")
        seller_wallet.balance -= req.usdt_amount
        seller_wallet.locked += req.usdt_amount
    db.flush()
    trade_ref = "SS-" + "".join(random.choices(string.digits, k=8))
    expires_at = datetime.utcnow() + timedelta(minutes=ad.trade_window)
    trade = models.Trade(
        trade_ref=trade_ref, ad_id=ad.id, buyer_id=buyer_id, seller_id=seller_id,
        usdt_amount=req.usdt_amount, inr_amount=inr_amount, rate=ad.rate,
        network=ad.network, payment_method=req.payment_method,
        status=models.TradeStatus.PENDING, escrow_locked_at=datetime.utcnow(), timer_expires_at=expires_at
    )
    db.add(trade)
    ad.available_usdt -= req.usdt_amount
    if ad.available_usdt <= 0:
        ad.is_active = False
    msg = models.TradeMessage(trade_id=trade.id, sender_id=None, content=f"Trade started. {req.usdt_amount} USDT locked in escrow. Pay ₹{inr_amount:,.2f} within {ad.trade_window} minutes.", msg_type="system")
    db.add(msg)
    db.commit()
    db.refresh(trade)
    buyer = db.query(models.User).filter(models.User.id == buyer_id).first()
    seller = db.query(models.User).filter(models.User.id == seller_id).first()
    send_notification(buyer.telegram_id, f"⚡ Trade #{trade_ref} started! Pay ₹{inr_amount:,.2f} within {ad.trade_window} min.")
    send_notification(seller.telegram_id, f"⚡ New trade #{trade_ref}! {req.usdt_amount} USDT locked in escrow.")
    return {"trade_ref": trade_ref, "trade": _trade_response(trade)}

@router.post("/{trade_ref}/mark-paid")
def mark_paid(trade_ref: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trade = _get_trade_or_404(trade_ref, db)
    if current_user.id != trade.buyer_id:
        raise HTTPException(status_code=403, detail="Only buyer can mark as paid")
    if trade.status != models.TradeStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"Trade is {trade.status}")
    if not trade.payment_proof:
        raise HTTPException(status_code=400, detail="Upload payment proof first")
    trade.status = models.TradeStatus.PAID
    trade.buyer_confirmed = True
    trade.buyer_confirmed_at = datetime.utcnow()
    trade.timer_expires_at = datetime.utcnow() + timedelta(minutes=settings.TRADE_AUTO_DISPUTE_MINUTES)
    _add_system_message(db, trade.id, "Buyer confirmed payment sent. Seller please verify and confirm.")
    db.commit()
    seller = db.query(models.User).filter(models.User.id == trade.seller_id).first()
    send_notification(seller.telegram_id, f"💸 Buyer confirmed payment for #{trade_ref}! Please verify ₹{trade.inr_amount:,.2f} and release.")
    return {"status": "paid", "message": "Payment marked. Waiting for seller confirmation."}

@router.post("/{trade_ref}/seller-confirm")
def seller_confirm(trade_ref: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trade = _get_trade_or_404(trade_ref, db)
    if current_user.id != trade.seller_id:
        raise HTTPException(status_code=403, detail="Only seller can confirm")
    if trade.status != models.TradeStatus.PAID:
        raise HTTPException(status_code=400, detail="Buyer must mark payment first")
    trade.seller_confirmed = True
    trade.seller_confirmed_at = datetime.utcnow()
    trade.status = models.TradeStatus.CONFIRMING
    _add_system_message(db, trade.id, "Seller confirmed. Processing release...")
    db.commit()
    if trade.buyer_confirmed and trade.seller_confirmed:
        _release_escrow(trade, db)
    return {"status": "confirmed", "message": "Both confirmed. USDT released!"}

@router.post("/{trade_ref}/upload-proof")
def upload_proof(trade_ref: str, file: UploadFile = File(...), current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trade = _get_trade_or_404(trade_ref, db)
    if current_user.id != trade.buyer_id:
        raise HTTPException(status_code=403, detail="Only buyer can upload proof")
    if trade.status != models.TradeStatus.PENDING:
        raise HTTPException(status_code=400, detail="Trade not in PENDING state")
    allowed = ["image/jpeg", "image/png", "image/webp", "application/pdf"]
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Only JPG, PNG, WebP, PDF allowed")
    contents = file.file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 10MB.")
    ext = file.filename.split(".")[-1]
    filename = f"{trade_ref}_{int(datetime.utcnow().timestamp())}.{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, "wb") as f:
        f.write(contents)
    trade.payment_proof = path
    _add_system_message(db, trade.id, f"Payment proof uploaded: {file.filename}")
    db.commit()
    return {"message": "Proof uploaded", "filename": filename}

@router.post("/{trade_ref}/dispute")
def raise_dispute(trade_ref: str, req: DisputeRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trade = _get_trade_or_404(trade_ref, db)
    if current_user.id not in [trade.buyer_id, trade.seller_id]:
        raise HTTPException(status_code=403, detail="Not a party to this trade")
    if trade.status not in [models.TradeStatus.PENDING, models.TradeStatus.PAID, models.TradeStatus.CONFIRMING]:
        raise HTTPException(status_code=400, detail=f"Cannot dispute a {trade.status} trade")
    if trade.dispute:
        raise HTTPException(status_code=400, detail="Dispute already raised")
    trade.status = models.TradeStatus.DISPUTED
    dispute = models.Dispute(trade_id=trade.id, raised_by_id=current_user.id, reason=req.reason, notes=req.notes)
    db.add(dispute)
    _add_system_message(db, trade.id, "⚠️ Dispute raised. Escrow frozen. Admin will review.")
    db.commit()
    other_id = trade.seller_id if current_user.id == trade.buyer_id else trade.buyer_id
    other = db.query(models.User).filter(models.User.id == other_id).first()
    send_notification(other.telegram_id, f"⚠️ Dispute raised on trade #{trade_ref}. Admin will review.")
    admins = db.query(models.User).filter(models.User.tier == models.UserTier.ADMIN).all()
    for a in admins:
        send_notification(a.telegram_id, f"🚨 New dispute on #{trade_ref}. Reason: {req.reason}")
    return {"message": "Dispute raised. Admin will review within 24h."}

@router.get("/my/active")
def my_active_trades(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trades = db.query(models.Trade).filter(
        (models.Trade.buyer_id == current_user.id) | (models.Trade.seller_id == current_user.id),
        models.Trade.status.in_([models.TradeStatus.PENDING, models.TradeStatus.PAID, models.TradeStatus.CONFIRMING, models.TradeStatus.DISPUTED])
    ).all()
    return [_trade_response(t) for t in trades]

@router.get("/my/history")
def my_trade_history(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db), page: int = 1, limit: int = 20):
    trades = db.query(models.Trade).filter(
        (models.Trade.buyer_id == current_user.id) | (models.Trade.seller_id == current_user.id)
    ).order_by(models.Trade.created_at.desc()).offset((page-1)*limit).limit(limit).all()
    return [_trade_response(t) for t in trades]

@router.get("/{trade_ref}")
def get_trade(trade_ref: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trade = _get_trade_or_404(trade_ref, db)
    if current_user.id not in [trade.buyer_id, trade.seller_id]:
        raise HTTPException(status_code=403, detail="Not your trade")
    return _trade_response(trade)

@router.get("/{trade_ref}/messages")
def get_messages(trade_ref: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trade = _get_trade_or_404(trade_ref, db)
    if current_user.id not in [trade.buyer_id, trade.seller_id]:
        raise HTTPException(status_code=403, detail="Not your trade")
    msgs = db.query(models.TradeMessage).filter(models.TradeMessage.trade_id == trade.id).order_by(models.TradeMessage.created_at.asc()).all()
    return [{"id": m.id, "sender_id": m.sender_id, "content": m.content, "msg_type": m.msg_type, "created_at": str(m.created_at), "is_mine": m.sender_id == current_user.id} for m in msgs]

@router.post("/{trade_ref}/messages")
def send_message(trade_ref: str, req: SendMessageRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trade = _get_trade_or_404(trade_ref, db)
    if current_user.id not in [trade.buyer_id, trade.seller_id]:
        raise HTTPException(status_code=403, detail="Not your trade")
    if trade.status in [models.TradeStatus.COMPLETED, models.TradeStatus.CANCELLED]:
        raise HTTPException(status_code=400, detail="Trade closed. Chat is read-only.")
    msg = models.TradeMessage(trade_id=trade.id, sender_id=current_user.id, content=req.content, msg_type="text")
    db.add(msg)
    db.commit()
    db.refresh(msg)
    other_id = trade.seller_id if current_user.id == trade.buyer_id else trade.buyer_id
    other = db.query(models.User).filter(models.User.id == other_id).first()
    send_notification(other.telegram_id, f"💬 New message in trade #{trade_ref}:\n{req.content[:80]}")
    return {"id": msg.id, "created_at": str(msg.created_at)}

@router.post("/{trade_ref}/review")
def leave_review(trade_ref: str, req: ReviewRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    trade = _get_trade_or_404(trade_ref, db)
    if current_user.id not in [trade.buyer_id, trade.seller_id]:
        raise HTTPException(status_code=403, detail="Not your trade")
    if trade.status != models.TradeStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Can only review completed trades")
    reviewed_id = trade.seller_id if current_user.id == trade.buyer_id else trade.buyer_id
    existing = db.query(models.Review).filter(models.Review.trade_id == trade.id, models.Review.reviewer_id == current_user.id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already reviewed this trade")
    review = models.Review(trade_id=trade.id, reviewer_id=current_user.id, reviewed_id=reviewed_id, rating=req.rating, comment=req.comment)
    db.add(review)
    reviewed = db.query(models.User).filter(models.User.id == reviewed_id).first()
    all_reviews = db.query(models.Review).filter(models.Review.reviewed_id == reviewed_id).all()
    total_rating = sum(r.rating for r in all_reviews) + req.rating
    reviewed.avg_rating = round(total_rating / (len(all_reviews) + 1), 2)
    db.commit()
    return {"message": "Review submitted"}

def _get_trade_or_404(trade_ref: str, db: Session) -> models.Trade:
    trade = db.query(models.Trade).filter(models.Trade.trade_ref == trade_ref).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade

def _add_system_message(db: Session, trade_id: int, content: str):
    msg = models.TradeMessage(trade_id=trade_id, sender_id=None, content=content, msg_type="system")
    db.add(msg)

def _release_escrow(trade: models.Trade, db: Session):
    seller_wallet = db.query(models.Wallet).filter(models.Wallet.user_id == trade.seller_id, models.Wallet.network == trade.network).first()
    buyer_wallet = db.query(models.Wallet).filter(models.Wallet.user_id == trade.buyer_id, models.Wallet.network == trade.network).first()
    if not seller_wallet or not buyer_wallet:
        raise HTTPException(status_code=500, detail="Wallet error during release")
    platform_fee = round(trade.usdt_amount * settings.PLATFORM_FEE_PERCENT / 100, 6)
    buyer_receives = trade.usdt_amount - platform_fee
    seller_wallet.locked -= trade.usdt_amount
    buyer_wallet.balance += buyer_receives
    trade.platform_fee = platform_fee
    trade.status = models.TradeStatus.COMPLETED
    trade.completed_at = datetime.utcnow()
    _update_user_stats(trade.buyer_id, trade, db)
    _update_user_stats(trade.seller_id, trade, db)
    _add_system_message(db, trade.id, f"✅ Trade complete! {buyer_receives} USDT released to buyer.")
    db.commit()
    buyer = db.query(models.User).filter(models.User.id == trade.buyer_id).first()
    seller = db.query(models.User).filter(models.User.id == trade.seller_id).first()
    send_notification(buyer.telegram_id, f"✅ {buyer_receives} USDT received! Trade #{trade.trade_ref} complete.")
    send_notification(seller.telegram_id, f"✅ Trade #{trade.trade_ref} complete. ₹{trade.inr_amount:,.2f} should be in your account.")

def _update_user_stats(user_id: int, trade: models.Trade, db: Session):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        return
    user.total_trades += 1
    user.completed_trades += 1
    user.total_volume += trade.usdt_amount
    if user.completed_trades >= 40 and user.tier == models.UserTier.USER:
        user.tier = models.UserTier.VERIFIED
    if user_id == trade.seller_id and trade.seller_confirmed_at and trade.escrow_locked_at:
        elapsed = (trade.seller_confirmed_at - trade.escrow_locked_at).total_seconds()
        user.avg_release_time = elapsed if user.avg_release_time == 0 else (user.avg_release_time + elapsed) / 2

def _trade_response(trade: models.Trade) -> dict:
    return {
        "trade_ref": trade.trade_ref, "status": trade.status,
        "usdt_amount": trade.usdt_amount, "inr_amount": trade.inr_amount,
        "rate": trade.rate, "network": trade.network, "payment_method": trade.payment_method,
        "buyer_id": trade.buyer_id, "seller_id": trade.seller_id,
        "buyer_confirmed": trade.buyer_confirmed, "seller_confirmed": trade.seller_confirmed,
        "has_proof": trade.payment_proof is not None,
        "timer_expires_at": str(trade.timer_expires_at) if trade.timer_expires_at else None,
        "completed_at": str(trade.completed_at) if trade.completed_at else None,
        "created_at": str(trade.created_at),
    }
