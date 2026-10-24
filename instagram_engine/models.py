import enum
from datetime import datetime, timezone
from typing import Optional, Any, Dict

from sqlalchemy import (
    String,
    Integer,
    BigInteger,
    Text,
    Float,
    DateTime,
    Enum,
    UniqueConstraint,
    Index,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    sessionmaker,
    scoped_session,
)

from instagram_engine.config import settings


class Base(DeclarativeBase):
    pass


class AccountRole(str, enum.Enum):
    SCRAPER = "SCRAPER"
    SENDER = "SENDER"


class AccountStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    COOLDOWN = "COOLDOWN"
    CHALLENGED = "CHALLENGED"
    BANNED = "BANNED"


class LeadStatus(str, enum.Enum):
    NEW = "NEW"
    DM_SENT = "DM_SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class IGAccount(Base):
    __tablename__ = "ig_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[AccountRole] = mapped_column(
        Enum(AccountRole, name="account_role_enum", native_enum=False),
        nullable=False,
        index=True
    )
    proxy_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    session_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    device_settings: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="account_status_enum", native_enum=False),
        default=AccountStatus.ACTIVE,
        nullable=False,
        index=True
    )
    daily_action_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_action_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    __table_args__ = (
        Index("ix_ig_accounts_tenant_role_status", "tenant_id", "role", "status"),
    )

    def is_available(self) -> bool:
        """Returns True if the account is active and past its cooldown window."""
        if self.status != AccountStatus.ACTIVE:
            return False
        if self.cooldown_until and self.cooldown_until > datetime.now(timezone.utc):
            return False
        return True


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ig_user_pk: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    profile_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    comment_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_post_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    post_caption_snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    matched_cta: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    intent_score: Mapped[float] = mapped_column(Float, default=0.8, nullable=False, index=True)
    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus, name="lead_status_enum", native_enum=False),
        default=LeadStatus.NEW,
        nullable=False,
        index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    __table_args__ = (
        # Strict deduplication guard per tenant: never scrape/message the same user twice
        UniqueConstraint("tenant_id", "ig_user_pk", name="uq_tenant_lead_pk"),
        Index("ix_leads_tenant_status", "tenant_id", "status"),
    )


# Database Engine and Session Factory
engine = create_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionLocal = scoped_session(
    sessionmaker(autocommit=False, autoflush=False, bind=engine)
)


def init_db():
    """Create tables if they do not exist."""
    Base.metadata.create_all(bind=engine)
