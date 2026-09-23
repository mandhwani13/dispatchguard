"""Dispatch business logic, verification, and gate pass reconciliation."""
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from app.models import DispatchOrder, Carton, ScanLog, OrderStatus, ScanResultEnum, utc_now
from app.schemas import OrderCreate, CartonCreate, CartonBulkCreate
from app.services.qr_service import (
    generate_qr_payload,
    parse_and_verify_payload,
)


def create_order(db: Session, order_in: OrderCreate) -> DispatchOrder:
    """Create a new dispatch order in PENDING status."""
    order = DispatchOrder(
        order_number=order_in.order_number.strip().upper(),
        customer_name=order_in.customer_name.strip(),
        transporter_name=order_in.transporter_name.strip() if order_in.transporter_name else None,
        vehicle_number=order_in.vehicle_number.strip().upper() if order_in.vehicle_number else None,
        expected_cartons=order_in.expected_cartons,
        expected_pieces=order_in.expected_pieces,
        scanned_cartons=0,
        scanned_pieces=0,
        status=OrderStatus.PENDING,
        created_at=utc_now(),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def get_order(db: Session, order_id: int) -> Optional[DispatchOrder]:
    """Retrieve an order by ID."""
    return db.query(DispatchOrder).filter(DispatchOrder.id == order_id).first()


def get_order_by_number(db: Session, order_number: str) -> Optional[DispatchOrder]:
    """Retrieve an order by order_number."""
    return db.query(DispatchOrder).filter(DispatchOrder.order_number == order_number.strip().upper()).first()


def get_all_orders(db: Session, status: Optional[str] = None) -> List[DispatchOrder]:
    """List orders sorted by creation time descending."""
    query = db.query(DispatchOrder)
    if status and status.upper() in OrderStatus.__members__:
        query = query.filter(DispatchOrder.status == OrderStatus[status.upper()])
    return query.order_by(desc(DispatchOrder.created_at)).all()


def get_active_orders_for_loading(db: Session) -> List[DispatchOrder]:
    """List orders available for loading dock scanner (PACKING, LOADING, PENDING)."""
    return (
        db.query(DispatchOrder)
        .filter(DispatchOrder.status.in_([OrderStatus.PENDING, OrderStatus.PACKING, OrderStatus.LOADING]))
        .order_by(desc(DispatchOrder.created_at))
        .all()
    )


def add_carton(
    db: Session,
    order: DispatchOrder,
    piece_count: int,
    carton_number: Optional[int] = None,
    notes: Optional[str] = None,
) -> Carton:
    """Add a single carton to the packing station and generate its serialized QR payload."""
    if carton_number is None:
        max_no = (
            db.query(func.max(Carton.carton_number))
            .filter(Carton.order_id == order.id)
            .scalar()
            or 0
        )
        carton_number = max_no + 1

    payload = generate_qr_payload(order.id, carton_number, piece_count)
    carton = Carton(
        order_id=order.id,
        carton_number=carton_number,
        piece_count=piece_count,
        qr_payload=payload,
        is_scanned=False,
        notes=notes.strip() if notes else None,
    )
    db.add(carton)

    if order.status == OrderStatus.PENDING:
        order.status = OrderStatus.PACKING

    db.commit()
    db.refresh(carton)
    return carton


def add_bulk_cartons(
    db: Session,
    order: DispatchOrder,
    num_cartons: int,
    pieces_per_carton: int,
    notes: Optional[str] = None,
) -> List[Carton]:
    """Add multiple cartons in batch with auto-incrementing numbers."""
    max_no = (
        db.query(func.max(Carton.carton_number))
        .filter(Carton.order_id == order.id)
        .scalar()
        or 0
    )

    created_cartons = []
    for i in range(1, num_cartons + 1):
        carton_no = max_no + i
        payload = generate_qr_payload(order.id, carton_no, pieces_per_carton)
        carton = Carton(
            order_id=order.id,
            carton_number=carton_no,
            piece_count=pieces_per_carton,
            qr_payload=payload,
            is_scanned=False,
            notes=notes.strip() if notes else None,
        )
        db.add(carton)
        created_cartons.append(carton)

    if order.status == OrderStatus.PENDING:
        order.status = OrderStatus.PACKING

    db.commit()
    for c in created_cartons:
        db.refresh(c)
    return created_cartons


def process_scan(
    db: Session,
    target_order_id: int,
    raw_payload: str,
) -> Dict[str, Any]:
    """
    Process a camera QR scan at the loading dock.
    Enforces payload integrity, order matching, duplicate scan prevention, and updates tally.
    """
    order = db.query(DispatchOrder).filter(DispatchOrder.id == target_order_id).first()
    if not order:
        return {
            "success": False,
            "scan_result": ScanResultEnum.CORRUPT_PAYLOAD,
            "message": f"Order #{target_order_id} does not exist",
            "carton_number": None,
            "piece_count": None,
            "scanned_cartons": 0,
            "expected_cartons": 0,
            "scanned_pieces": 0,
            "expected_pieces": 0,
            "is_order_complete": False,
            "carton_progress_pct": 0,
            "piece_progress_pct": 0,
            "order_status": OrderStatus.PENDING,
        }

    # Step 1: Parse and verify QR code format and HMAC signature
    is_valid, payload_order_id, carton_no, piece_count, error_msg = parse_and_verify_payload(raw_payload)

    if not is_valid:
        # Corrupt or forged QR
        log = ScanLog(
            order_id=order.id,
            carton_id=None,
            raw_scanned_payload=raw_payload,
            scan_result=ScanResultEnum.CORRUPT_PAYLOAD,
            created_at=utc_now(),
        )
        db.add(log)
        db.commit()
        return {
            "success": False,
            "scan_result": ScanResultEnum.CORRUPT_PAYLOAD,
            "message": f"Scan Rejected: {error_msg}",
            "carton_number": carton_no,
            "piece_count": piece_count,
            "scanned_cartons": order.scanned_cartons,
            "expected_cartons": order.expected_cartons,
            "scanned_pieces": order.scanned_pieces,
            "expected_pieces": order.expected_pieces,
            "is_order_complete": order.is_fully_loaded,
            "carton_progress_pct": order.carton_progress_pct,
            "piece_progress_pct": order.piece_progress_pct,
            "order_status": order.status,
        }

    # Step 2: Verify order matching
    if payload_order_id != order.id:
        log = ScanLog(
            order_id=order.id,
            carton_id=None,
            raw_scanned_payload=raw_payload,
            scan_result=ScanResultEnum.WRONG_ORDER,
            created_at=utc_now(),
        )
        db.add(log)
        db.commit()
        return {
            "success": False,
            "scan_result": ScanResultEnum.WRONG_ORDER,
            "message": f"WRONG DISPATCH! Carton #{carton_no} belongs to Order ID #{payload_order_id}, not current Order #{order.order_number}!",
            "carton_number": carton_no,
            "piece_count": piece_count,
            "scanned_cartons": order.scanned_cartons,
            "expected_cartons": order.expected_cartons,
            "scanned_pieces": order.scanned_pieces,
            "expected_pieces": order.expected_pieces,
            "is_order_complete": order.is_fully_loaded,
            "carton_progress_pct": order.carton_progress_pct,
            "piece_progress_pct": order.piece_progress_pct,
            "order_status": order.status,
        }

    # Step 3: Check if order is already completed / locked
    if order.status == OrderStatus.COMPLETED:
        return {
            "success": False,
            "scan_result": ScanResultEnum.DUPLICATE,
            "message": f"Order #{order.order_number} is already COMPLETED and Gate Pass generated. Scanning is locked.",
            "carton_number": carton_no,
            "piece_count": piece_count,
            "scanned_cartons": order.scanned_cartons,
            "expected_cartons": order.expected_cartons,
            "scanned_pieces": order.scanned_pieces,
            "expected_pieces": order.expected_pieces,
            "is_order_complete": True,
            "carton_progress_pct": 100,
            "piece_progress_pct": 100,
            "order_status": order.status,
        }

    # Step 4: Retrieve matching carton in database
    carton = (
        db.query(Carton)
        .filter(Carton.order_id == order.id, Carton.carton_number == carton_no)
        .first()
    )

    if not carton:
        log = ScanLog(
            order_id=order.id,
            carton_id=None,
            raw_scanned_payload=raw_payload,
            scan_result=ScanResultEnum.CORRUPT_PAYLOAD,
            created_at=utc_now(),
        )
        db.add(log)
        db.commit()
        return {
            "success": False,
            "scan_result": ScanResultEnum.CORRUPT_PAYLOAD,
            "message": f"Box #{carton_no} was not found in packing records for Order #{order.order_number}",
            "carton_number": carton_no,
            "piece_count": piece_count,
            "scanned_cartons": order.scanned_cartons,
            "expected_cartons": order.expected_cartons,
            "scanned_pieces": order.scanned_pieces,
            "expected_pieces": order.expected_pieces,
            "is_order_complete": order.is_fully_loaded,
            "carton_progress_pct": order.carton_progress_pct,
            "piece_progress_pct": order.piece_progress_pct,
            "order_status": order.status,
        }

    # Step 5: Duplicate scan prevention
    if carton.is_scanned:
        scan_time_str = carton.scanned_at.strftime("%H:%M:%S") if carton.scanned_at else "earlier"
        log = ScanLog(
            order_id=order.id,
            carton_id=carton.id,
            raw_scanned_payload=raw_payload,
            scan_result=ScanResultEnum.DUPLICATE,
            created_at=utc_now(),
        )
        db.add(log)
        db.commit()
        return {
            "success": False,
            "scan_result": ScanResultEnum.DUPLICATE,
            "message": f"DUPLICATE SCAN! Box #{carton_no} ({carton.piece_count} pcs) was already loaded at {scan_time_str}!",
            "carton_number": carton_no,
            "piece_count": carton.piece_count,
            "scanned_cartons": order.scanned_cartons,
            "expected_cartons": order.expected_cartons,
            "scanned_pieces": order.scanned_pieces,
            "expected_pieces": order.expected_pieces,
            "is_order_complete": order.is_fully_loaded,
            "carton_progress_pct": order.carton_progress_pct,
            "piece_progress_pct": order.piece_progress_pct,
            "order_status": order.status,
        }

    # Step 6: Valid scan - mark loaded
    carton.is_scanned = True
    carton.scanned_at = utc_now()

    # Recalculate tallies atomically
    scanned_count = (
        db.query(func.count(Carton.id))
        .filter(Carton.order_id == order.id, Carton.is_scanned == True)
        .scalar()
        or 0
    ) + 1  # include the current carton being committed

    scanned_pcs = (
        db.query(func.sum(Carton.piece_count))
        .filter(Carton.order_id == order.id, Carton.is_scanned == True)
        .scalar()
        or 0
    ) + carton.piece_count

    order.scanned_cartons = scanned_count
    order.scanned_pieces = scanned_pcs

    if order.status in [OrderStatus.PENDING, OrderStatus.PACKING]:
        order.status = OrderStatus.LOADING

    # Check if fully reconciled
    is_complete = (
        order.expected_cartons > 0
        and order.scanned_cartons >= order.expected_cartons
        and order.scanned_pieces >= order.expected_pieces
    )

    if is_complete and order.status != OrderStatus.COMPLETED:
        order.status = OrderStatus.COMPLETED
        order.completed_at = utc_now()

    # Create successful scan log
    log = ScanLog(
        order_id=order.id,
        carton_id=carton.id,
        raw_scanned_payload=raw_payload,
        scan_result=ScanResultEnum.SUCCESS,
        created_at=utc_now(),
    )
    db.add(log)
    db.commit()
    db.refresh(order)

    return {
        "success": True,
        "scan_result": ScanResultEnum.SUCCESS,
        "message": f"Box #{carton_no} Verified & Loaded ({carton.piece_count} PCS)",
        "carton_number": carton_no,
        "piece_count": carton.piece_count,
        "scanned_cartons": order.scanned_cartons,
        "expected_cartons": order.expected_cartons,
        "scanned_pieces": order.scanned_pieces,
        "expected_pieces": order.expected_pieces,
        "is_order_complete": is_complete,
        "carton_progress_pct": order.carton_progress_pct,
        "piece_progress_pct": order.piece_progress_pct,
        "order_status": order.status,
    }


def complete_and_lock_order(db: Session, order_id: int) -> Tuple[bool, str, Optional[DispatchOrder]]:
    """Transition order to COMPLETED and lock from further scans if verified."""
    order = get_order(db, order_id)
    if not order:
        return False, "Order not found", None

    if order.scanned_cartons != order.expected_cartons:
        return False, f"Carton mismatch: Scanned {order.scanned_cartons} of {order.expected_cartons}", order

    if order.scanned_pieces != order.expected_pieces:
        return False, f"Piece mismatch: Scanned {order.scanned_pieces} of {order.expected_pieces}", order

    order.status = OrderStatus.COMPLETED
    if not order.completed_at:
        order.completed_at = utc_now()
    db.commit()
    db.refresh(order)
    return True, "Order successfully verified, completed and locked.", order


def seed_demo_data(db: Session) -> Dict[str, Any]:
    """Seed comprehensive realistic manufacturing warehouse data."""
    # Check if already seeded
    existing = db.query(DispatchOrder).first()
    if existing:
        return {"message": "Data already seeded", "count": db.query(DispatchOrder).count()}

    # 1. Order 1: Ready for loading dock scanning demo
    order1 = DispatchOrder(
        order_number="DG-2026-0891",
        customer_name="Apex Retail Global Ltd.",
        transporter_name="BlueDart Express Cargo",
        vehicle_number="MH-04-AB-9821",
        expected_cartons=6,
        expected_pieces=300,
        scanned_cartons=0,
        scanned_pieces=0,
        status=OrderStatus.PACKING,
        created_at=utc_now(),
    )
    db.add(order1)
    db.flush()

    for i in range(1, 7):
        payload = generate_qr_payload(order1.id, i, 50)
        c = Carton(
            order_id=order1.id,
            carton_number=i,
            piece_count=50,
            qr_payload=payload,
            is_scanned=False,
            notes=f"Lot A{i} - Standard Pack",
        )
        db.add(c)

    # 2. Order 2: Completed order with Gate Pass generated
    order2 = DispatchOrder(
        order_number="DG-2026-0740",
        customer_name="Metro Departmental Stores",
        transporter_name="TCI Freight Lines",
        vehicle_number="DL-1L-CX-4042",
        expected_cartons=4,
        expected_pieces=200,
        scanned_cartons=4,
        scanned_pieces=200,
        status=OrderStatus.COMPLETED,
        created_at=utc_now(),
        completed_at=utc_now(),
    )
    db.add(order2)
    db.flush()

    for i in range(1, 5):
        payload = generate_qr_payload(order2.id, i, 50)
        c = Carton(
            order_id=order2.id,
            carton_number=i,
            piece_count=50,
            qr_payload=payload,
            is_scanned=True,
            scanned_at=utc_now(),
            notes="Checked & Cleared",
        )
        db.add(c)
        db.flush()
        log = ScanLog(
            order_id=order2.id,
            carton_id=c.id,
            raw_scanned_payload=payload,
            scan_result=ScanResultEnum.SUCCESS,
            created_at=utc_now(),
        )
        db.add(log)

    # 3. Order 3: New Pending Order
    order3 = DispatchOrder(
        order_number="DG-2026-0902",
        customer_name="Zenith Apparel & Exports",
        transporter_name="V-Trans Logistics",
        vehicle_number="KA-01-MJ-5519",
        expected_cartons=8,
        expected_pieces=480,
        scanned_cartons=0,
        scanned_pieces=0,
        status=OrderStatus.PENDING,
        created_at=utc_now(),
    )
    db.add(order3)

    db.commit()
    return {"message": "Successfully seeded demo dispatch orders with packed cartons"}
