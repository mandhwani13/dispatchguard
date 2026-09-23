"""Pydantic v2 Schemas for DispatchGuard."""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from app.models import OrderStatus, ScanResultEnum


class OrderCreate(BaseModel):
    order_number: str = Field(..., min_length=2, max_length=50, description="Unique Order Number / Invoice ID")
    customer_name: str = Field(..., min_length=2, max_length=150, description="Customer or Party Name")
    transporter_name: Optional[str] = Field(None, max_length=100, description="Transporter or Carrier Name")
    vehicle_number: Optional[str] = Field(None, max_length=50, description="Vehicle Registration Number")
    expected_cartons: int = Field(..., gt=0, description="Total expected cartons")
    expected_pieces: int = Field(..., gt=0, description="Total expected pieces")


class CartonCreate(BaseModel):
    carton_number: Optional[int] = Field(None, gt=0, description="Carton number (auto-incremented if null)")
    piece_count: int = Field(..., gt=0, description="Quantity of pieces in this carton")
    notes: Optional[str] = Field(None, max_length=255, description="Optional size or item notes")


class CartonBulkCreate(BaseModel):
    num_cartons: int = Field(..., gt=0, le=500, description="Number of cartons to generate")
    pieces_per_carton: int = Field(..., gt=0, description="Pieces per carton")
    notes: Optional[str] = Field(None, max_length=255, description="Notes for this batch")


class CartonOut(BaseModel):
    id: int
    order_id: int
    carton_number: int
    piece_count: int
    qr_payload: str
    is_scanned: bool
    scanned_at: Optional[datetime] = None
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class OrderOut(BaseModel):
    id: int
    order_number: str
    customer_name: str
    transporter_name: Optional[str] = None
    vehicle_number: Optional[str] = None
    expected_cartons: int
    expected_pieces: int
    scanned_cartons: int
    scanned_pieces: int
    status: OrderStatus
    created_at: datetime
    completed_at: Optional[datetime] = None
    total_packed_cartons: int
    total_packed_pieces: int

    model_config = ConfigDict(from_attributes=True)


class ScanVerifyRequest(BaseModel):
    order_id: int = Field(..., description="Active truck dispatch order ID being loaded")
    raw_payload: str = Field(..., min_length=5, description="Scanned QR code string")


class ScanVerifyResponse(BaseModel):
    success: bool
    scan_result: ScanResultEnum
    message: str
    carton_number: Optional[int] = None
    piece_count: Optional[int] = None
    scanned_cartons: int
    expected_cartons: int
    scanned_pieces: int
    expected_pieces: int
    is_order_complete: bool
    carton_progress_pct: int
    piece_progress_pct: int
    order_status: OrderStatus
