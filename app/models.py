"""Database models for DispatchGuard."""
import enum
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    ForeignKey,
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship
from app.database import Base


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    PACKING = "PACKING"
    LOADING = "LOADING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ScanResultEnum(str, enum.Enum):
    SUCCESS = "SUCCESS"
    DUPLICATE = "DUPLICATE"
    WRONG_ORDER = "WRONG_ORDER"
    CORRUPT_PAYLOAD = "CORRUPT_PAYLOAD"


def utc_now():
    return datetime.now(timezone.utc)


class DispatchOrder(Base):
    __tablename__ = "dispatch_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(50), unique=True, index=True, nullable=False)
    customer_name = Column(String(150), nullable=False)
    transporter_name = Column(String(100), nullable=True)
    vehicle_number = Column(String(50), nullable=True)
    expected_cartons = Column(Integer, nullable=False, default=0)
    expected_pieces = Column(Integer, nullable=False, default=0)
    scanned_cartons = Column(Integer, default=0, nullable=False)
    scanned_pieces = Column(Integer, default=0, nullable=False)
    status = Column(
        SQLEnum(OrderStatus, native_enum=False, values_callable=lambda obj: [e.value for e in obj]),
        default=OrderStatus.PENDING,
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    cartons = relationship(
        "Carton",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="Carton.carton_number",
    )
    scan_logs = relationship(
        "ScanLog",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="desc(ScanLog.created_at)",
    )

    @property
    def total_packed_cartons(self) -> int:
        return len(self.cartons)

    @property
    def total_packed_pieces(self) -> int:
        return sum(c.piece_count for c in self.cartons)

    @property
    def carton_progress_pct(self) -> int:
        if self.expected_cartons <= 0:
            return 0
        return min(100, int((self.scanned_cartons / self.expected_cartons) * 100))

    @property
    def piece_progress_pct(self) -> int:
        if self.expected_pieces <= 0:
            return 0
        return min(100, int((self.scanned_pieces / self.expected_pieces) * 100))

    @property
    def is_fully_loaded(self) -> bool:
        return (
            self.expected_cartons > 0
            and self.scanned_cartons >= self.expected_cartons
            and self.scanned_pieces >= self.expected_pieces
        )


class Carton(Base):
    __tablename__ = "cartons"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(
        Integer,
        ForeignKey("dispatch_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    carton_number = Column(Integer, nullable=False)
    piece_count = Column(Integer, nullable=False)
    qr_payload = Column(Text, unique=True, nullable=False, index=True)
    is_scanned = Column(Boolean, default=False, nullable=False)
    scanned_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(String(255), nullable=True)

    # Relationships
    order = relationship("DispatchOrder", back_populates="cartons")
    scan_logs = relationship("ScanLog", back_populates="carton")


class ScanLog(Base):
    __tablename__ = "scan_logs"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(
        Integer,
        ForeignKey("dispatch_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    carton_id = Column(
        Integer,
        ForeignKey("cartons.id", ondelete="SET NULL"),
        nullable=True,
    )
    raw_scanned_payload = Column(Text, nullable=False)
    scan_result = Column(
        SQLEnum(ScanResultEnum, native_enum=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Relationships
    order = relationship("DispatchOrder", back_populates="scan_logs")
    carton = relationship("Carton", back_populates="scan_logs")
