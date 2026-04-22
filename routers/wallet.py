from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, validator
from datetime import datetime
from typing import Optional
from database import get_db
from security import get_current_user, check_withdrawal_cooldown, check_daily_withdrawal_limit, check_idempotency
from config import settings
import models
from bot import send_notification

router = APIRouter()

LIVE_RATE = 84.30  # Replace with live rate fetch if needed

class WithdrawRequest(BaseModel):
    network: models.Network
    to_address: str
    amount: float

    @validator("amount")
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be positive")
        return round(v, 6)

@router.get("/balance")
def get_balance(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Single balance endpoint for frontend compatibility"""
    wallets = db.query(models.Wallet).filter(models.Wallet.user_id == current_user.id).all()
    total_available = sum(w.balance for w in wallets)
    total_locked = sum(w.locked for w in wallets)
    return {
        "balance": round(total_available, 6),
        "inr_value": round(total_available * LIVE_RATE, 2),
        "locked": round(total_locked, 6),
    }

@router.get("/balances")
def get_balances(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallets = db.query(models.Wallet).filter(models.Wallet.user_id == current_user.id).all()
    total_available = sum(w.balance for w in wallets)
    total_locked = sum(w.locked for w in wallets)
    return {
        "total_available": round(total_available, 6),
        "total_locked": round(total_locked, 6),
        "inr_value": round(total_available * LIVE_RATE, 2),
        "wallets": [
            {
                "network": w.network,
                "address": w.address,
                "available": round(w.balance, 6),
                "locked": round(w.locked, 6)
            } for w in wallets
        ]
    }

@router.get("/deposit-addresses")
def get_deposit_addresses(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallets = db.query(models.Wallet).filter(models.Wallet.user_id == current_user.id).all()
    return {
        w.network: {
            "address": w.address,
            "confirmations_req": settings.CONFIRMATIONS.get(w.network.value, 12) if hasattr(settings, 'CONFIRMATIONS') else 12
        } for w in wallets
    }

@router.post("/withdraw")
def withdraw(req: WithdrawRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    check_withdrawal_cooldown(current_user.telegram_id)
    check_daily_withdrawal_limit(current_user.id, req.amount, db)
    check_idempotency(f"withdraw:{current_user.id}:{req.amount}:{req.to_address}", ttl=30)
    wallet = db.query(models.Wallet).filter(
        models.Wallet.user_id == current_user.id,
        models.Wallet.network == req.network
    ).first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    gas_fee = _estimate_gas_fee(req.network)
    total_deduct = req.amount + gas_fee
    if wallet.balance < total_deduct:
        raise HTTPException(status_code=400, detail=f"Insufficient balance. Need {total_deduct} USDT")
    wallet.balance -= total_deduct
    withdrawal = models.Withdrawal(
        user_id=current_user.id,
        network=req.network,
        to_address=req.to_address,
        amount=req.amount,
        gas_fee=gas_fee,
        platform_fee=0.0,
        status="PENDING"
    )
    db.add(withdrawal)
    db.commit()
    db.refresh(withdrawal)
    send_notification(current_user.telegram_id, f"⏳ Withdrawal of {req.amount} USDT ({req.network.value}) submitted.")
    return {
        "id": withdrawal.id,
        "status": "PENDING",
        "amount": req.amount,
        "gas_fee": gas_fee,
        "message": "Withdrawal submitted."
    }

@router.get("/deposits")
def get_deposit_history(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet_ids = [w.id for w in db.query(models.Wallet).filter(models.Wallet.user_id == current_user.id).all()]
    deposits = db.query(models.Deposit).filter(
        models.Deposit.wallet_id.in_(wallet_ids)
    ).order_by(models.Deposit.created_at.desc()).limit(50).all()
    return [
        {
            "txid": d.txid,
            "amount": d.amount,
            "network": d.network,
            "confirmations": d.confirmations,
            "credited": d.credited,
            "created_at": str(d.created_at)
        } for d in deposits
    ]

@router.get("/withdrawals")
def get_withdrawal_history(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    withdrawals = db.query(models.Withdrawal).filter(
        models.Withdrawal.user_id == current_user.id
    ).order_by(models.Withdrawal.created_at.desc()).limit(50).all()
    return [
        {
            "id": w.id,
            "amount": w.amount,
            "gas_fee": w.gas_fee,
            "network": w.network,
            "to_address": w.to_address,
            "status": w.status,
            "txid": w.txid,
            "created_at": str(w.created_at)
        } for w in withdrawals
    ]

def _estimate_gas_fee(network: models.Network) -> float:
    fees = {
        models.Network.TRC20: 1.0,
        models.Network.BEP20: 0.5,
        models.Network.POLYGON: 0.1,
        models.Network.SOLANA: 0.01,
        models.Network.ARBITRUM: 0.5,
        models.Network.OPTIMISM: 0.5,
        models.Network.ERC20: 5.0
    }
    return fees.get(network, 1.0)
    
