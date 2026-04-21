from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, validator
from datetime import datetime, timedelta
from typing import Optional, List
import json
from database import get_db
from security import get_current_user, check_ad_rate_limit
import models

router = APIRouter()

class PostAdRequest(BaseModel):
    ad_type: models.AdType
    network: models.Network
    rate: float
    min_amount_inr: float
    max_amount_inr: float
    total_usdt: float
    payment_methods: List[models.PaymentMethod]
    trade_window: int
    terms: Optional[str] = None

@router.post("/post")
def post_ad(req: PostAdRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    check_ad_rate_limit(current_user.telegram_id)
    from config import settings
    if current_user.total_trades < 5:
        max_inr = req.total_usdt * req.rate
        if max_inr > settings.NEW_USER_MAX_TRADE_INR:
            raise HTTPException(status_code=400, detail=f"New accounts limited to ₹{settings.NEW_USER_MAX_TRADE_INR:,.0f} per ad")
    if req.ad_type == models.AdType.SELL:
        wallet = db.query(models.Wallet).filter(models.Wallet.user_id == current_user.id, models.Wallet.network == req.network).first()
        if not wallet or wallet.balance < req.total_usdt:
            raise HTTPException(status_code=400, detail=f"Insufficient {req.network.value} balance.")
    ad = models.Ad(
        user_id=current_user.id, ad_type=req.ad_type, network=req.network,
        rate=req.rate, min_amount_inr=req.min_amount_inr, max_amount_inr=req.max_amount_inr,
        total_usdt=req.total_usdt, available_usdt=req.total_usdt,
        payment_methods=json.dumps([m.value for m in req.payment_methods]),
        trade_window=req.trade_window, terms=req.terms, is_active=True,
        expires_at=datetime.utcnow() + timedelta(hours=24)
    )
    db.add(ad)
    db.commit()
    db.refresh(ad)
    return {"id": ad.id, "message": "Ad posted successfully"}

@router.get("/list")
def list_ads(ad_type: Optional[models.AdType] = Query(None), network: Optional[models.Network] = Query(None), page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    query = db.query(models.Ad).filter(models.Ad.is_active == True, models.Ad.is_paused == False, models.Ad.available_usdt > 0, models.Ad.expires_at > datetime.utcnow())
    if ad_type:
        query = query.filter(models.Ad.ad_type == ad_type)
    if network:
        query = query.filter(models.Ad.network == network)
    query = query.order_by(models.Ad.is_featured.desc(), models.Ad.created_at.desc())
    total = query.count()
    ads = query.offset((page - 1) * limit).limit(limit).all()
    return {"total": total, "page": page, "ads": [_ad_response(ad) for ad in ads]}

@router.get("/my-ads")
def my_ads(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    ads = db.query(models.Ad).filter(models.Ad.user_id == current_user.id).order_by(models.Ad.created_at.desc()).all()
    return [_ad_response(ad) for ad in ads]

@router.delete("/{ad_id}")
def delete_ad(ad_id: int, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    ad = db.query(models.Ad).filter(models.Ad.id == ad_id, models.Ad.user_id == current_user.id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    ad.is_active = False
    db.commit()
    return {"message": "Ad removed"}

@router.patch("/{ad_id}/pause")
def toggle_pause(ad_id: int, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    ad = db.query(models.Ad).filter(models.Ad.id == ad_id, models.Ad.user_id == current_user.id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    ad.is_paused = not ad.is_paused
    db.commit()
    return {"paused": ad.is_paused}

def _ad_response(ad: models.Ad) -> dict:
    user = ad.user
    return {
        "id": ad.id, "ad_type": ad.ad_type, "network": ad.network,
        "rate": ad.rate, "min_amount_inr": ad.min_amount_inr, "max_amount_inr": ad.max_amount_inr,
        "available_usdt": ad.available_usdt, "payment_methods": json.loads(ad.payment_methods),
        "trade_window": ad.trade_window, "terms": ad.terms, "is_featured": ad.is_featured,
        "expires_at": str(ad.expires_at), "created_at": str(ad.created_at),
        "user": {"id": user.id, "username": user.username, "first_name": user.first_name, "tier": user.tier, "completion_rate": user.completion_rate, "total_trades": user.total_trades, "avg_rating": user.avg_rating}
    }
