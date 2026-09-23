"""DispatchGuard - Main FastAPI Application."""
import os
from contextlib import asynccontextmanager
from typing import Optional, List
from fastapi import FastAPI, Depends, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import settings
from app.database import engine, Base, get_db
from app.models import DispatchOrder, Carton, ScanLog, OrderStatus, ScanResultEnum
from app.schemas import OrderCreate, ScanVerifyRequest, ScanVerifyResponse
from app.services import qr_service, dispatch


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler for frictionless first-time boot on Render & local."""
    # Auto-create tables
    Base.metadata.create_all(bind=engine)
    
    # Auto-seed initial demo data if database is brand new
    try:
        from app.database import SessionLocal
        with SessionLocal() as db:
            dispatch.seed_demo_data(db)
    except Exception as e:
        print(f"Startup seed notice: {e}")

    yield


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="Ready-Stock Dispatch & Carton Verification Gate-Pass System",
    lifespan=lifespan,
)

# Static and Templates mounting
base_dir = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(base_dir, "static")
templates_dir = os.path.join(base_dir, "templates")

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)


# ---------------------------------------------------------
# Health Check Endpoint
# ---------------------------------------------------------
@app.get("/healthz", tags=["System"])
def health_check():
    return {"status": "healthy", "app": settings.APP_NAME, "env": settings.APP_ENV}


# ---------------------------------------------------------
# Web UI Pages (Jinja2 Rendered)
# ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse, tags=["Web UI"])
def dashboard(
    request: Request,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Dashboard: list active dispatches, stats counters, filter by status."""
    orders = dispatch.get_all_orders(db, status=status)

    total_orders = db.query(func.count(DispatchOrder.id)).scalar() or 0
    packing_orders = db.query(func.count(DispatchOrder.id)).filter(DispatchOrder.status.in_([OrderStatus.PENDING, OrderStatus.PACKING])).scalar() or 0
    loading_orders = db.query(func.count(DispatchOrder.id)).filter(DispatchOrder.status == OrderStatus.LOADING).scalar() or 0
    completed_orders = db.query(func.count(DispatchOrder.id)).filter(DispatchOrder.status == OrderStatus.COMPLETED).scalar() or 0

    return templates.TemplateResponse(
        request=request,
        name="orders_list.html",
        context={
            "orders": orders,
            "current_status": status.upper() if status else None,
            "total_orders": total_orders,
            "packing_orders": packing_orders,
            "loading_orders": loading_orders,
            "completed_orders": completed_orders,
        },
    )


@app.get("/orders/{order_id}/packing", response_class=HTMLResponse, tags=["Web UI"])
def packing_station(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Packing station: view cartons, add individual/bulk, preview stickers."""
    order = dispatch.get_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    next_carton_no = (
        db.query(func.max(Carton.carton_number))
        .filter(Carton.order_id == order.id)
        .scalar()
        or 0
    ) + 1

    remaining_cartons = max(0, order.expected_cartons - len(order.cartons))
    default_pieces_per_carton = (
        max(1, order.expected_pieces // order.expected_cartons)
        if order.expected_cartons > 0
        else 50
    )

    return templates.TemplateResponse(
        request=request,
        name="packing.html",
        context={
            "order": order,
            "next_carton_no": next_carton_no,
            "remaining_cartons": remaining_cartons,
            "default_pieces_per_carton": default_pieces_per_carton,
        },
    )


@app.get("/orders/{order_id}/stickers", response_class=HTMLResponse, tags=["Web UI"])
def print_thermal_stickers(
    order_id: int,
    request: Request,
    carton_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Printable thermal label layout formatted for 4"x2" or 3"x2" rolls."""
    order = dispatch.get_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if carton_id:
        cartons = [c for c in order.cartons if c.id == carton_id]
        if not cartons:
            raise HTTPException(status_code=404, detail="Carton not found in this order")
    else:
        cartons = order.cartons

    return templates.TemplateResponse(
        request=request,
        name="stickers_print.html",
        context={
            "order": order,
            "cartons": cartons,
        },
    )


@app.get("/scanner", response_class=HTMLResponse, tags=["Web UI"])
def loading_dock_scanner(
    request: Request,
    order_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Loading dock mobile camera verification interface."""
    active_orders = dispatch.get_active_orders_for_loading(db)
    
    selected_order = None
    if order_id:
        selected_order = dispatch.get_order(db, order_id)
    elif active_orders:
        selected_order = active_orders[0]

    return templates.TemplateResponse(
        request=request,
        name="scanner.html",
        context={
            "active_orders": active_orders,
            "selected_order": selected_order,
        },
    )


@app.get("/orders/{order_id}/gate-pass", response_class=HTMLResponse, tags=["Web UI"])
def gate_pass_view(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Digital Gate Pass summary page and A4 printable document."""
    order = dispatch.get_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return templates.TemplateResponse(
        request=request,
        name="gate_pass.html",
        context={
            "order": order,
        },
    )


# ---------------------------------------------------------
# REST & Form API Endpoints
# ---------------------------------------------------------
@app.post("/api/orders", tags=["Orders"])
def create_dispatch_order(
    order_number: str = Form(...),
    customer_name: str = Form(...),
    expected_cartons: int = Form(...),
    expected_pieces: int = Form(...),
    transporter_name: Optional[str] = Form(None),
    vehicle_number: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Create a new dispatch order from supervisor form or API."""
    existing = dispatch.get_order_by_number(db, order_number)
    if existing:
        return RedirectResponse(url=f"/orders/{existing.id}/packing", status_code=status.HTTP_303_SEE_OTHER)

    order_in = OrderCreate(
        order_number=order_number,
        customer_name=customer_name,
        transporter_name=transporter_name,
        vehicle_number=vehicle_number,
        expected_cartons=expected_cartons,
        expected_pieces=expected_pieces,
    )
    order = dispatch.create_order(db, order_in)
    return RedirectResponse(url=f"/orders/{order.id}/packing", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/api/orders/{order_id}/cartons", tags=["Cartons"])
def pack_single_carton(
    order_id: int,
    piece_count: int = Form(...),
    carton_number: Optional[int] = Form(None),
    notes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Add an individual carton and generate serialized QR payload."""
    order = dispatch.get_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    dispatch.add_carton(
        db=db,
        order=order,
        piece_count=piece_count,
        carton_number=carton_number,
        notes=notes,
    )
    return RedirectResponse(url=f"/orders/{order.id}/packing", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/api/orders/{order_id}/cartons/bulk", tags=["Cartons"])
def pack_bulk_cartons(
    order_id: int,
    num_cartons: int = Form(...),
    pieces_per_carton: int = Form(...),
    notes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Generate multiple cartons in batch with auto-incrementing numbers."""
    order = dispatch.get_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    dispatch.add_bulk_cartons(
        db=db,
        order=order,
        num_cartons=num_cartons,
        pieces_per_carton=pieces_per_carton,
        notes=notes,
    )
    return RedirectResponse(url=f"/orders/{order.id}/packing", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/api/cartons/{carton_id}/qr.png", tags=["Cartons"])
def get_carton_qr_image(
    carton_id: int,
    db: Session = Depends(get_db),
):
    """Stream high-contrast PNG QR image for the carton sticker."""
    carton = db.query(Carton).filter(Carton.id == carton_id).first()
    if not carton:
        raise HTTPException(status_code=404, detail="Carton not found")

    png_bytes = qr_service.generate_qr_png_bytes(carton.qr_payload)
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.post("/api/scan/verify", response_model=ScanVerifyResponse, tags=["Scanner"])
def verify_carton_scan(
    scan_req: ScanVerifyRequest,
    db: Session = Depends(get_db),
):
    """Mobile loading dock verification endpoint."""
    res = dispatch.process_scan(
        db=db,
        target_order_id=scan_req.order_id,
        raw_payload=scan_req.raw_payload,
    )
    return res


@app.post("/api/orders/{order_id}/complete", tags=["Gate Pass"])
def finalize_order(
    order_id: int,
    db: Session = Depends(get_db),
):
    """Finalize order and lock from further scans once verified."""
    success, message, order = dispatch.complete_and_lock_order(db, order_id)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return RedirectResponse(url=f"/orders/{order_id}/gate-pass", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/api/demo/seed", tags=["System"])
def seed_demo_endpoint(db: Session = Depends(get_db)):
    """Seed test data and redirect to dashboard."""
    dispatch.seed_demo_data(db)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
