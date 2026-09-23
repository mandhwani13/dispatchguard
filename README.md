# DispatchGuard 🛡️📦

> **Ready-Stock Dispatch & Carton Verification Gate-Pass System for Manufacturing Warehouses**

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/mandhwani13/dispatchguard)

DispatchGuard is a mobile-first, production-grade warehouse dispatch application. It eliminates dock loading errors, prevents wrong-truck dispatches, thwarts duplicate carton loading, and enforces 100% carton & piece count reconciliation before issuing authenticated digital Gate Passes.

---

## 🚀 Key Features & Workflows

### 1. Workflow A: Dispatch Order Creation (Office / Supervisor)
- Fast order entry with Invoice/Order ID, Customer/Party Name, Transporter, Vehicle Registration Number, and Expected Carton & Piece counts.
- Immediate real-time status tracking (`PENDING` ➔ `PACKING` ➔ `LOADING` ➔ `COMPLETED`).

### 2. Workflow B: Packing Station & Thermal Sticker Generation
- Auto-incrementing carton serialization (`BOX # 1 of 12`, `BOX # 2 of 12`).
- Bulk Carton Generator: generate 10, 20, or 100 uniform cartons with piece counts in a single click.
- Cryptographically signed QR codes using HMAC-SHA256 (`DISPATCH|{order_id}|{carton_no}|{piece_count}|{hash}`).
- Thermal Label Printing: CSS optimized for standard **4" x 2"** and **3" x 2"** thermal roll sticker printers via browser `window.print()`.

### 3. Workflow C: Loading Dock Verification (Mobile Scanner)
- Mobile-first, responsive viewport designed for budget Android devices over Chrome.
- Fast camera scanning powered by `html5-qrcode` with rear/front camera toggle and torch/flashlight support.
- **Audio Feedback (Web Audio API Synthesizer)**:
  - **Success Chime**: Dual-harmonic pleasant chime (880 Hz ➔ 1760 Hz).
  - **Error Buzzer**: Industrial low-pitch sawtooth warning buzzer (150 Hz) for wrong truck or duplicate cartons.
- **Haptic Vibration**: Vibration alert (`navigator.vibrate([200, 100, 200])`) on errors.
- **Vivid Industrial Red Modal**: Full-screen alert screen requiring worker acknowledgement when an invalid scan is detected.
- Live HUD displaying real-time tally: `"Scanned 8 / 12 Cartons | 420 / 600 Pieces"`.

### 4. Workflow D: Gate Pass Generation & Reconciliation
- Strict zero-variance gate pass lock: unlocks only when scanned cartons = expected cartons **AND** scanned pieces = expected pieces.
- Digital Sign-off: Supervisor badge, driver receipt, and security clearance QR code.
- Print-ready formatted for standard **A4** document or receipt printing.

---

## 🛠️ Technology Stack

- **Backend**: Python 3.11+ / 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2.
- **Database**: PostgreSQL (with connection pooling via `psycopg2-binary`) or SQLite fallback for zero-config local testing.
- **Frontend**: Server-rendered Jinja2 templates styled with Tailwind CSS (CDN) and Lucide Icons.
- **QR Engine**: `qrcode` + `Pillow` for PNG barcode generation; `html5-qrcode` for mobile camera decoding.
- **Deployment**: Dockerfile + Render Blueprint (`render.yaml`) with Managed PostgreSQL.

---

## 💻 Local Setup & Quickstart

### 1. Clone & Enter Directory
```bash
cd dispatchguard
```

### 2. Create Virtual Environment & Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Run the Development Server
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Open your browser at **`http://localhost:8000`**.

> **Note on Initial Boot**: On startup, DispatchGuard automatically initializes database tables and seeds demo dispatch orders with sample packed cartons.

---

## 📱 Testing the Mobile Scanner

1. Open `http://localhost:8000` or your LAN IP (e.g. `http://192.168.1.X:8000`) on your mobile browser.
2. Select an active order (e.g. `DG-2026-0891`).
3. Point your camera at any thermal label generated on the packing page.
4. **Desktop / Simulator Testing**: You can also paste the QR code string directly into the **Manual Input** box at the bottom of the scanner page!
   - Example valid QR string: `DISPATCH|1|1|50|<VALID_HASH>`
   - Try scanning the same box twice to trigger the duplicate detection alarm and red alert modal.
   - Try scanning a box belonging to another order to test the wrong-truck defense.

---

## 🚢 100% Free Render Cloud Deployment

Render recently started charging ($7/mo) for managed PostgreSQL instances in blueprints. To keep DispatchGuard **100% Free with zero credit card required**:

1. Click the **Deploy to Render** button above or use:
   👉 **[Deploy to Render (Free Tier)](https://render.com/deploy?repo=https://github.com/mandhwani13/dispatchguard)**
2. Select the **Free Plan** (`plan: free`). Render will deploy the web service at **$0 / month** with no payment details needed!
3. By default, DispatchGuard automatically runs with zero-config SQLite.
4. **(Optional) Free Cloud PostgreSQL**:
   If you want a free hosted PostgreSQL database without paying Render, you can create a free database on [Neon.tech](https://neon.tech) or [Supabase.com](https://supabase.com) (both 100% free forever, no credit card required), copy the Connection String, and paste it into the `DATABASE_URL` environment variable on Render!

---

## 🧪 Running Automated Tests

DispatchGuard includes an automated test suite verifying all cryptographic checks, carton generation, and scan verification rules:

```bash
source .venv/bin/activate
pytest tests/ -v
```
