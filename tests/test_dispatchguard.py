"""Automated test suite for DispatchGuard."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.models import OrderStatus, ScanResultEnum, DispatchOrder, Carton, ScanLog
from app.schemas import OrderCreate
from app.services import qr_service, dispatch
from app.main import app

# Test database setup (in-memory SQLite with StaticPool so all connections share the memory db)
TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=test_engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(db_session):
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# =========================================================================
# QR & Cryptographic Signature Tests
# =========================================================================
def test_qr_payload_generation_and_tamper_detection():
    order_id = 42
    carton_no = 3
    piece_count = 55

    payload = qr_service.generate_qr_payload(order_id, carton_no, piece_count)
    assert payload.startswith("DISPATCH|42|3|55|")

    # Valid payload check
    is_valid, o_id, c_no, pcs, err = qr_service.parse_and_verify_payload(payload)
    assert is_valid is True
    assert o_id == 42
    assert c_no == 3
    assert pcs == 55
    assert err == ""

    # Tampered piece count
    tampered_payload = f"DISPATCH|42|3|99|{payload.split('|')[4]}"
    is_valid, _, _, _, err = qr_service.parse_and_verify_payload(tampered_payload)
    assert is_valid is False
    assert "signature mismatch" in err.lower()

    # Corrupt string format
    is_valid, _, _, _, err = qr_service.parse_and_verify_payload("RANDOM_TEXT_NOT_QR")
    assert is_valid is False


def test_qr_png_generation():
    payload = qr_service.generate_qr_payload(1, 1, 20)
    png_bytes = qr_service.generate_qr_png_bytes(payload)
    assert len(png_bytes) > 100
    # PNG signature header: \x89PNG\r\n\x1a\n
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")


# =========================================================================
# Order & Carton Workflow Business Logic Tests
# =========================================================================
def test_order_creation_and_carton_batching(db_session):
    order_in = OrderCreate(
        order_number="ORD-TEST-001",
        customer_name="Test Customer Ltd",
        transporter_name="Swift Cargo",
        vehicle_number="DL-01-AB-1234",
        expected_cartons=3,
        expected_pieces=150,
    )
    order = dispatch.create_order(db_session, order_in)
    assert order.id is not None
    assert order.status == OrderStatus.PENDING
    assert order.scanned_cartons == 0
    assert order.scanned_pieces == 0

    # Add single carton
    c1 = dispatch.add_carton(db_session, order, piece_count=50)
    assert c1.carton_number == 1
    assert c1.piece_count == 50
    assert order.status == OrderStatus.PACKING

    # Add bulk cartons
    bulk_cartons = dispatch.add_bulk_cartons(db_session, order, num_cartons=2, pieces_per_carton=50)
    assert len(bulk_cartons) == 2
    assert bulk_cartons[0].carton_number == 2
    assert bulk_cartons[1].carton_number == 3

    assert order.total_packed_cartons == 3
    assert order.total_packed_pieces == 150


def test_loading_dock_scan_verification_lifecycle(db_session):
    # Setup test order with 2 cartons (50 pieces each)
    order = dispatch.create_order(
        db_session,
        OrderCreate(
            order_number="ORD-LOAD-002",
            customer_name="Loading Warehouse",
            expected_cartons=2,
            expected_pieces=100,
        ),
    )
    c1 = dispatch.add_carton(db_session, order, piece_count=50)
    c2 = dispatch.add_carton(db_session, order, piece_count=50)

    # 1. Valid scan on Carton 1
    res1 = dispatch.process_scan(db_session, order.id, c1.qr_payload)
    assert res1["success"] is True
    assert res1["scan_result"] == ScanResultEnum.SUCCESS
    assert res1["scanned_cartons"] == 1
    assert res1["scanned_pieces"] == 50
    assert res1["is_order_complete"] is False

    # 2. Duplicate scan on Carton 1 (Must be rejected!)
    res_dup = dispatch.process_scan(db_session, order.id, c1.qr_payload)
    assert res_dup["success"] is False
    assert res_dup["scan_result"] == ScanResultEnum.DUPLICATE
    assert "DUPLICATE SCAN" in res_dup["message"]
    # Ensure tally was NOT incremented on duplicate
    assert res_dup["scanned_cartons"] == 1
    assert res_dup["scanned_pieces"] == 50

    # 3. Wrong Order Scan (Create second order and try scanning its box)
    order2 = dispatch.create_order(
        db_session,
        OrderCreate(
            order_number="ORD-OTHER-003",
            customer_name="Other Party",
            expected_cartons=1,
            expected_pieces=25,
        ),
    )
    c_other = dispatch.add_carton(db_session, order2, piece_count=25)

    res_wrong = dispatch.process_scan(db_session, order.id, c_other.qr_payload)
    assert res_wrong["success"] is False
    assert res_wrong["scan_result"] == ScanResultEnum.WRONG_ORDER
    assert "WRONG DISPATCH" in res_wrong["message"]

    # 4. Valid scan on Carton 2 -> Reconciles 100% and triggers completion
    res2 = dispatch.process_scan(db_session, order.id, c2.qr_payload)
    assert res2["success"] is True
    assert res2["scanned_cartons"] == 2
    assert res2["scanned_pieces"] == 100
    assert res2["is_order_complete"] is True
    assert res2["order_status"] == OrderStatus.COMPLETED

    # 5. Gate Pass lock test: further scan attempts on completed order
    res_locked = dispatch.process_scan(db_session, order.id, c1.qr_payload)
    assert res_locked["success"] is False


# =========================================================================
# Web API & HTTP Endpoints Tests
# =========================================================================
def test_http_api_endpoints(client, db_session):
    # Test health check
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"

    # Test dashboard page
    res = client.get("/")
    assert res.status_code == 200
    assert "Ready-Stock Dispatch Manifests" in res.text

    # Test create order via POST
    post_res = client.post(
        "/api/orders",
        data={
            "order_number": "HTTP-TEST-99",
            "customer_name": "API Client Corp",
            "expected_cartons": 2,
            "expected_pieces": 80,
            "transporter_name": "FastTrack",
            "vehicle_number": "MH-12-QQ-1111",
        },
        follow_redirects=False,
    )
    assert post_res.status_code == 303
    order_id = int(post_res.headers["location"].split("/")[2])

    # Test packing station page
    pack_page_res = client.get(f"/orders/{order_id}/packing")
    assert pack_page_res.status_code == 200
    assert "HTTP-TEST-99" in pack_page_res.text

    # Test adding single carton via API
    add_carton_res = client.post(
        f"/api/orders/{order_id}/cartons",
        data={"piece_count": 40, "notes": "Test box"},
        follow_redirects=False,
    )
    assert add_carton_res.status_code == 303

    # Test bulk cartons via API
    bulk_res = client.post(
        f"/api/orders/{order_id}/cartons/bulk",
        data={"num_cartons": 1, "pieces_per_carton": 40, "notes": "Bulk box"},
        follow_redirects=False,
    )
    assert bulk_res.status_code == 303

    # Fetch order to get carton id and QR payload
    order = dispatch.get_order(db_session, order_id)
    carton = order.cartons[0]
    carton2 = order.cartons[1]

    # Test QR image endpoint
    qr_res = client.get(f"/api/cartons/{carton.id}/qr.png")
    assert qr_res.status_code == 200
    assert qr_res.headers["content-type"] == "image/png"

    # Test thermal stickers page
    stickers_res = client.get(f"/orders/{order_id}/stickers")
    assert stickers_res.status_code == 200
    assert "DISPATCHGUARD" in stickers_res.text

    # Test mobile scanner page
    scanner_res = client.get(f"/scanner?order_id={order_id}")
    assert scanner_res.status_code == 200
    assert "LOADING DOCK LIVE TALLY" in scanner_res.text

    # Test mobile scan verification endpoint (valid scan)
    scan_res = client.post(
        "/api/scan/verify",
        json={"order_id": order.id, "raw_payload": carton.qr_payload},
    )
    assert scan_res.status_code == 200
    scan_json = scan_res.json()
    assert scan_json["success"] is True
    assert scan_json["scanned_cartons"] == 1
    assert scan_json["scanned_pieces"] == 40

    # Test mobile scan verification endpoint (duplicate scan)
    dup_res = client.post(
        "/api/scan/verify",
        json={"order_id": order.id, "raw_payload": carton.qr_payload},
    )
    assert dup_res.status_code == 200
    assert dup_res.json()["success"] is False
    assert dup_res.json()["scan_result"] == "DUPLICATE"

    # Test mobile scan verification endpoint (corrupt payload)
    corrupt_res = client.post(
        "/api/scan/verify",
        json={"order_id": order.id, "raw_payload": "GARBAGE_PAYLOAD"},
    )
    assert corrupt_res.status_code == 200
    assert corrupt_res.json()["success"] is False
    assert corrupt_res.json()["scan_result"] == "CORRUPT_PAYLOAD"

    # Scan 2nd carton to 100% completion
    scan_res2 = client.post(
        "/api/scan/verify",
        json={"order_id": order.id, "raw_payload": carton2.qr_payload},
    )
    assert scan_res2.status_code == 200
    assert scan_res2.json()["is_order_complete"] is True

    # Test gate pass page
    gate_res = client.get(f"/orders/{order_id}/gate-pass")
    assert gate_res.status_code == 200
    assert "OFFICIAL DISPATCH GATE PASS" in gate_res.text
    assert "SECURITY CLEARED" in gate_res.text
