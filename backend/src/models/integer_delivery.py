from datetime import datetime
from sqlalchemy import String, ForeignKey, JSON, DateTime, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class IntegerDelivery(Base):
    """一次整数化求解的归档：同一运行同一放松范围只保留最新一份。"""
    __tablename__ = 'integer_deliveries'
    __table_args__ = (UniqueConstraint('run_id', 'relax', name='uq_integer_run_relax'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('weight_runs.id'))
    relax: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default='solved')  # solved / failed
    failure_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
