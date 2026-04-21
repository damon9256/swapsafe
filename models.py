from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import enum

class UserTier(str, enum.Enum):
    USER = "USER"
    VERIFIED = "VERIFIED"
    MERCHANT = "MERCHANT"
    VIP_MERCHANT = "VIP_MERCHANT"
    TRUSTED = "TRUSTED"
    ADMIN = "ADMIN"

class TradeStatus(str, enum.Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    CONFIRMING = "CONFIRMING"
    COMPLETED = "COMPLETED"
    DISPUTED = "DISPUTED"
    CANCELLED = "CANCELLED"
    RESOLVED = "RESOLVED"

class AdType(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"

class Network(str, enum.Enum):
    TRC20 = "TRC20"
    ERC20 = "ERC20"
    BEP20 = "BEP20"
    POLYGON = "POLYGON"
    SOLANA = "SOLANA"
    ARBITRUM = "ARBITRUM"
    OPTIMISM = "OPTIMISM"

class PaymentMethod(str, enum.Enum):
    UPI = "UPI"
    CASH_DEPOSIT = "CASH_DEPOSIT"
    DIGITAL_ERUPEE = "DIGITAL_ERUPEE"

class DisputeOutcome(str, enum.Enum):
    PENDING = "PENDING"
    BUYER_WON = "BUYER_WON"
    SELLER_WON = "SELLER_WON"
    SPLIT = "SPLIT"

class User(Base):
    __tablename__ = "users"
    id               = Column(Integer, primary_key=True, index=True)
    telegram_id      = Column(String, unique=True, index=True, nullable=False)
    username         = Column(String, nullable=True)
    first_name       = Column(String, nullable=True)
    last_name        = Column(String, nullable=True)
    pin_hash         = Column(String, nullable=True)
    tier             = Column(Enum(UserTier), default=UserTier.USER)
    is_frozen        = Column(Boolean, default=False)
    is_banned        = Column(Boolean, default=False)
    total_trades     = Column(Integer, default=0)
    completed_trades = Column(Integer, default=0)
    disputed_trades  = Column(Integer, default=0)
    avg_release_time = Column(Float, default=0.0)
    avg_rating       = Column(Float, default=0.0)
    total_volume     = Column(Float, default=0.0)
    last_seen        = Column(DateTime(timezone=True), server_default=func.now())
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    wallets          = relationship("Wallet", back_populates="user")
    ads              = relationship("Ad", back_populates="user")
    buy_trades       = relationship("Trade", foreign_keys="Trade.buyer_id", back_populates="buyer")
    sell_trades      = relationship("Trade", foreign_keys="Trade.seller_id", back_populates="seller")
    reviews_received = relationship("Review", foreign_keys="Review.reviewed_id", back_populates="reviewed")
    reviews_given    = relationship("Review", foreign_keys="Review.reviewer_id", back_populates="reviewer")
    notifications    = relationship("Notification", back_populates="user")

    @property
    def completion_rate(self):
        if self.total_trades == 0:
            return 0.0
        return round((self.completed_trades / self.total_trades) * 100, 1)

class Wallet(Base):
    __tablename__ = "wallets"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    network    = Column(Enum(Network), nullable=False)
    address    = Column(String, unique=True, nullable=False)
    balance    = Column(Float, default=0.0)
    locked     = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user       = relationship("User", back_populates="wallets")
    deposits   = relationship("Deposit", back_populates="wallet")

class Deposit(Base):
    __tablename__ = "deposits"
    id            = Column(Integer, primary_key=True, index=True)
    wallet_id     = Column(Integer, ForeignKey("wallets.id"), nullable=False)
    txid          = Column(String, unique=True, nullable=False, index=True)
    amount        = Column(Float, nullable=False)
    network       = Column(Enum(Network), nullable=False)
    confirmations = Column(Integer, default=0)
    required_conf = Column(Integer, nullable=False)
    credited      = Column(Boolean, default=False)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    credited_at   = Column(DateTime(timezone=True), nullable=True)
    wallet        = relationship("Wallet", back_populates="deposits")

class Withdrawal(Base):
    __tablename__ = "withdrawals"
    id           = Column(Integer, primary_key=True, index=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    network      = Column(Enum(Network), nullable=False)
    to_address   = Column(String, nullable=False)
    amount       = Column(Float, nullable=False)
    gas_fee      = Column(Float, default=0.0)
    platform_fee = Column(Float, default=0.0)
    txid         = Column(String, nullable=True)
    status       = Column(String, default="PENDING")
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)
    user         = relationship("User")

class Ad(Base):
    __tablename__ = "ads"
    id              = Column(Integer, primary_key=True, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=False)
    ad_type         = Column(Enum(AdType), nullable=False)
    network         = Column(Enum(Network), nullable=False)
    rate            = Column(Float, nullable=False)
    min_amount_inr  = Column(Float, nullable=False)
    max_amount_inr  = Column(Float, nullable=False)
    total_usdt      = Column(Float, nullable=False)
    available_usdt  = Column(Float, nullable=False)
    payment_methods = Column(String, nullable=False)
    trade_window    = Column(Integer, default=30)
    terms           = Column(Text, nullable=True)
    is_active       = Column(Boolean, default=True)
    is_featured     = Column(Boolean, default=False)
    is_paused       = Column(Boolean, default=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    expires_at      = Column(DateTime(timezone=True), nullable=True)
    user            = relationship("User", back_populates="ads")
    trades          = relationship("Trade", back_populates="ad")

class Trade(Base):
    __tablename__ = "trades"
    id                  = Column(Integer, primary_key=True, index=True)
    trade_ref           = Column(String, unique=True, index=True)
    ad_id               = Column(Integer, ForeignKey("ads.id"), nullable=False)
    buyer_id            = Column(Integer, ForeignKey("users.id"), nullable=False)
    seller_id           = Column(Integer, ForeignKey("users.id"), nullable=False)
    usdt_amount         = Column(Float, nullable=False)
    inr_amount          = Column(Float, nullable=False)
    rate                = Column(Float, nullable=False)
    network             = Column(Enum(Network), nullable=False)
    payment_method      = Column(Enum(PaymentMethod), nullable=False)
    status              = Column(Enum(TradeStatus), default=TradeStatus.PENDING)
    platform_fee        = Column(Float, default=0.0)
    payment_proof       = Column(String, nullable=True)
    buyer_confirmed     = Column(Boolean, default=False)
    seller_confirmed    = Column(Boolean, default=False)
    buyer_confirmed_at  = Column(DateTime(timezone=True), nullable=True)
    seller_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    escrow_locked_at    = Column(DateTime(timezone=True), nullable=True)
    timer_expires_at    = Column(DateTime(timezone=True), nullable=True)
    completed_at        = Column(DateTime(timezone=True), nullable=True)
    cancelled_at        = Column(DateTime(timezone=True), nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    ad                  = relationship("Ad", back_populates="trades")
    buyer               = relationship("User", foreign_keys=[buyer_id], back_populates="buy_trades")
    seller              = relationship("User", foreign_keys=[seller_id], back_populates="sell_trades")
    messages            = relationship("TradeMessage", back_populates="trade")
    dispute             = relationship("Dispute", back_populates="trade", uselist=False)

class TradeMessage(Base):
    __tablename__ = "trade_messages"
    id         = Column(Integer, primary_key=True, index=True)
    trade_id   = Column(Integer, ForeignKey("trades.id"), nullable=False)
    sender_id  = Column(Integer, ForeignKey("users.id"), nullable=True)
    content    = Column(Text, nullable=False)
    msg_type   = Column(String, default="text")
    file_path  = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    trade      = relationship("Trade", back_populates="messages")
    sender     = relationship("User")

class Dispute(Base):
    __tablename__ = "disputes"
    id           = Column(Integer, primary_key=True, index=True)
    trade_id     = Column(Integer, ForeignKey("trades.id"), unique=True, nullable=False)
    raised_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason       = Column(String, nullable=False)
    notes        = Column(Text, nullable=True)
    evidence     = Column(String, nullable=True)
    outcome      = Column(Enum(DisputeOutcome), default=DisputeOutcome.PENDING)
    admin_notes  = Column(Text, nullable=True)
    resolved_by  = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at  = Column(DateTime(timezone=True), nullable=True)
    trade        = relationship("Trade", back_populates="dispute")
    raised_by    = relationship("User", foreign_keys=[raised_by_id])
    admin        = relationship("User", foreign_keys=[resolved_by])

class Review(Base):
    __tablename__ = "reviews"
    id          = Column(Integer, primary_key=True, index=True)
    trade_id    = Column(Integer, ForeignKey("trades.id"), nullable=False)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reviewed_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    rating      = Column(Integer, nullable=False)
    comment     = Column(String(150), nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    trade       = relationship("Trade")
    reviewer    = relationship("User", foreign_keys=[reviewer_id], back_populates="reviews_given")
    reviewed    = relationship("User", foreign_keys=[reviewed_id], back_populates="reviews_received")

class Notification(Base):
    __tablename__ = "notifications"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    title      = Column(String, nullable=False)
    body       = Column(Text, nullable=False)
    notif_type = Column(String, default="system")
    is_read    = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user       = relationship("User", back_populates="notifications")

class AdminLog(Base):
    __tablename__ = "admin_logs"
    id         = Column(Integer, primary_key=True, index=True)
    admin_id   = Column(Integer, ForeignKey("users.id"), nullable=False)
    action     = Column(String, nullable=False)
    target_id  = Column(Integer, nullable=True)
    details    = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    admin      = relationship("User")

class PlatformSettings(Base):
    __tablename__ = "platform_settings"
    id         = Column(Integer, primary_key=True, index=True)
    key        = Column(String, unique=True, nullable=False)
    value      = Column(String, nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
