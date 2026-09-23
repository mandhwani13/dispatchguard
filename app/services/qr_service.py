"""QR Code generation and payload verification service."""
import hmac
import hashlib
import io
from typing import Tuple, Optional
import qrcode
from app.config import settings


def compute_payload_hash(order_id: int, carton_no: int, piece_count: int) -> str:
    """Compute HMAC-SHA256 signature for carton authenticity verification."""
    data = f"{order_id}:{carton_no}:{piece_count}".encode("utf-8")
    signature = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        data,
        hashlib.sha256
    ).hexdigest()[:10].upper()
    return signature


def generate_qr_payload(order_id: int, carton_no: int, piece_count: int) -> str:
    """Generate serialized QR payload format: DISPATCH|{order_id}|{carton_no}|{piece_count}|{hash}"""
    sig = compute_payload_hash(order_id, carton_no, piece_count)
    return f"DISPATCH|{order_id}|{carton_no}|{piece_count}|{sig}"


def parse_and_verify_payload(raw_payload: str) -> Tuple[bool, Optional[int], Optional[int], Optional[int], str]:
    """
    Parses and cryptographically verifies the QR payload.
    Returns: (is_valid, order_id, carton_no, piece_count, error_reason)
    """
    if not raw_payload or not isinstance(raw_payload, str):
        return False, None, None, None, "Empty or invalid QR code format"

    clean_payload = raw_payload.strip()
    parts = clean_payload.split("|")
    if len(parts) != 5 or parts[0] != "DISPATCH":
        return False, None, None, None, "Invalid DispatchGuard QR signature or unrecognized format"

    try:
        order_id = int(parts[1])
        carton_no = int(parts[2])
        piece_count = int(parts[3])
    except ValueError:
        return False, None, None, None, "Corrupted numeric data in QR code"

    expected_hash = compute_payload_hash(order_id, carton_no, piece_count)
    provided_hash = parts[4].upper()

    if not hmac.compare_digest(expected_hash, provided_hash):
        return False, order_id, carton_no, piece_count, "Security signature mismatch (Tampered or forged QR code)"

    return True, order_id, carton_no, piece_count, ""


def generate_qr_png_bytes(payload: str, box_size: int = 8, border: int = 2) -> bytes:
    """Generate crisp PNG image bytes for a given QR string."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()
