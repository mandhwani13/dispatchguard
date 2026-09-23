/**
 * DispatchGuard Loading Dock Mobile Scanner & Audio Synth Engine
 */

let audioCtx = null;
let html5QrCode = null;
let isScanning = false;
let isTorchOn = false;
let isProcessingScan = false;
let currentCameraFacing = "environment"; // default to rear camera

// Initialize Audio Context on first user interaction
function initAudio() {
    if (!audioCtx) {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (AudioContextClass) {
            audioCtx = new AudioContextClass();
        }
    }
    if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume();
    }
}

/**
 * Play a high-pitched, pleasant double chime on success
 */
function playSuccessChime() {
    try {
        initAudio();
        if (!audioCtx) return;

        const now = audioCtx.currentTime;

        // Tone 1: 880 Hz (A5)
        const osc1 = audioCtx.createOscillator();
        const gain1 = audioCtx.createGain();
        osc1.type = 'sine';
        osc1.frequency.setValueAtTime(880, now);
        gain1.gain.setValueAtTime(0.2, now);
        gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.15);
        osc1.connect(gain1);
        gain1.connect(audioCtx.destination);
        osc1.start(now);
        osc1.stop(now + 0.15);

        // Tone 2: 1760 Hz (A6)
        const osc2 = audioCtx.createOscillator();
        const gain2 = audioCtx.createGain();
        osc2.type = 'sine';
        osc2.frequency.setValueAtTime(1760, now + 0.1);
        gain2.gain.setValueAtTime(0.25, now + 0.1);
        gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
        osc2.connect(gain2);
        gain2.connect(audioCtx.destination);
        osc2.start(now + 0.1);
        osc2.stop(now + 0.35);
    } catch (err) {
        console.warn("Audio synthesis error:", err);
    }
}

/**
 * Play a loud, low-pitch error buzzer
 */
function playErrorBuzzer() {
    try {
        initAudio();
        if (!audioCtx) return;

        const now = audioCtx.currentTime;
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();

        // Sawtooth wave for industrial warning sound
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(150, now);
        osc.frequency.linearRampToValueAtTime(110, now + 0.4);

        gain.gain.setValueAtTime(0.4, now);
        gain.gain.exponentialRampToValueAtTime(0.01, now + 0.45);

        osc.connect(gain);
        gain.connect(audioCtx.destination);

        osc.start(now);
        osc.stop(now + 0.45);
    } catch (err) {
        console.warn("Audio buzzer error:", err);
    }
}

/**
 * Trigger device haptic vibration
 */
function triggerVibration(pattern = [200, 100, 200]) {
    if (navigator.vibrate) {
        navigator.vibrate(pattern);
    }
}

/**
 * Display the Error Modal with detailed explanation
 */
function showErrorModal(title, message, code) {
    const modal = document.getElementById("scanErrorModal");
    const titleEl = document.getElementById("modalErrorTitle");
    const msgEl = document.getElementById("modalErrorMessage");
    const codeEl = document.getElementById("modalErrorCode");

    if (modal && titleEl && msgEl) {
        titleEl.textContent = title;
        msgEl.textContent = message;
        if (codeEl) codeEl.textContent = `CODE: ${code}`;
        modal.classList.remove("hidden");
    }
}

function dismissErrorModal() {
    const modal = document.getElementById("scanErrorModal");
    if (modal) {
        modal.classList.add("hidden");
    }
    // allow next scan after modal dismissal
    setTimeout(() => {
        isProcessingScan = false;
    }, 400);
}

/**
 * Display Success Banner
 */
function showSuccessToast(message, cartonNo, pieceCount) {
    const banner = document.getElementById("scanSuccessToast");
    const msgEl = document.getElementById("successToastMessage");
    const detailEl = document.getElementById("successToastDetail");

    if (banner && msgEl) {
        msgEl.textContent = message;
        if (detailEl) {
            detailEl.textContent = `Box #${cartonNo} • ${pieceCount} Pieces Loaded`;
        }
        banner.classList.remove("hidden");
        banner.classList.remove("opacity-0");
        banner.classList.add("opacity-100");

        setTimeout(() => {
            banner.classList.add("opacity-0");
            setTimeout(() => banner.classList.add("hidden"), 300);
        }, 2800);
    }
}

/**
 * Add an entry to the live scan activity feed
 */
function appendScanLogEntry(result, text, timestamp) {
    const feed = document.getElementById("recentScansFeed");
    if (!feed) return;

    // Remove empty placeholder if exists
    const placeholder = document.getElementById("noScansPlaceholder");
    if (placeholder) placeholder.remove();

    const isSuccess = result === "SUCCESS";
    const bgClass = isSuccess ? "bg-emerald-50 border-emerald-200 text-emerald-900" : "bg-red-50 border-red-200 text-red-900";
    const icon = isSuccess ? "✓" : "✗";

    const item = document.createElement("div");
    item.className = `p-2.5 rounded-lg border text-xs flex items-center justify-between shadow-sm animate-pulse ${bgClass}`;
    item.innerHTML = `
        <div class="flex items-center space-x-2 truncate">
            <span class="font-bold text-sm">${icon}</span>
            <span class="truncate font-medium">${text}</span>
        </div>
        <span class="text-[10px] text-gray-500 whitespace-nowrap ml-2">${timestamp || new Date().toLocaleTimeString()}</span>
    `;

    feed.prepend(item);
    setTimeout(() => item.classList.remove("animate-pulse"), 1000);
}

/**
 * Verify scanned QR code against the backend API
 */
async function verifyScannedCode(rawPayload) {
    if (isProcessingScan) return;
    isProcessingScan = true;

    const orderSelect = document.getElementById("orderSelect");
    const orderId = orderSelect ? parseInt(orderSelect.value, 10) : null;

    if (!orderId || isNaN(orderId)) {
        playErrorBuzzer();
        triggerVibration();
        showErrorModal("Order Not Selected", "Please select an active dispatch order before scanning cartons.", "NO_ORDER_SELECTED");
        return;
    }

    try {
        const response = await fetch("/api/scan/verify", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                order_id: orderId,
                raw_payload: rawPayload.trim(),
            }),
        });

        const data = await response.json();

        if (response.ok && data.success) {
            // 1. Audio Success Chime
            playSuccessChime();

            // 2. Light vibration
            if (navigator.vibrate) navigator.vibrate(80);

            // 3. Success Toast
            showSuccessToast(data.message, data.carton_number, data.piece_count);

            // 4. Update Live Tally UI
            updateTallyUI(data);

            // 5. Append to activity feed
            appendScanLogEntry("SUCCESS", `Box #${data.carton_number} (${data.piece_count} pcs) Verified`, new Date().toLocaleTimeString());

            // 6. Check if order fully loaded
            if (data.is_order_complete) {
                handleOrderCompleted(data);
            }

            // Brief throttle before next scan
            setTimeout(() => {
                isProcessingScan = false;
            }, 1200);

        } else {
            // Scan Rejected: Duplicate, Wrong Order, or Corrupted
            playErrorBuzzer();
            triggerVibration([200, 100, 200]);

            const title = data.scan_result === "DUPLICATE" 
                ? "Duplicate Carton Detected!" 
                : data.scan_result === "WRONG_ORDER" 
                ? "Wrong Dispatch Order!" 
                : "Invalid / Corrupted QR Code";

            showErrorModal(title, data.message || "Scanned code could not be verified.", data.scan_result || "REJECTED");

            appendScanLogEntry("ERROR", data.message || "Scan Rejected", new Date().toLocaleTimeString());
        }
    } catch (err) {
        console.error("Verification network error:", err);
        playErrorBuzzer();
        triggerVibration();
        showErrorModal("Network / Server Error", "Could not reach the dispatch verification server. Check your connection.", "NETWORK_ERROR");
    }
}

/**
 * Update the visual live tally card and progress bars
 */
function updateTallyUI(data) {
    const cartonsCountEl = document.getElementById("tallyScannedCartons");
    const cartonsTotalEl = document.getElementById("tallyExpectedCartons");
    const piecesCountEl = document.getElementById("tallyScannedPieces");
    const piecesTotalEl = document.getElementById("tallyExpectedPieces");

    const cartonBar = document.getElementById("cartonProgressBar");
    const pieceBar = document.getElementById("pieceProgressBar");

    const cartonPctText = document.getElementById("cartonPctText");
    const piecePctText = document.getElementById("piecePctText");

    if (cartonsCountEl) cartonsCountEl.textContent = data.scanned_cartons;
    if (cartonsTotalEl) cartonsTotalEl.textContent = data.expected_cartons;
    if (piecesCountEl) piecesCountEl.textContent = data.scanned_pieces;
    if (piecesTotalEl) piecesTotalEl.textContent = data.expected_pieces;

    if (cartonBar) cartonBar.style.width = `${data.carton_progress_pct}%`;
    if (pieceBar) pieceBar.style.width = `${data.piece_progress_pct}%`;

    if (cartonPctText) cartonPctText.textContent = `${data.carton_progress_pct}%`;
    if (piecePctText) piecePctText.textContent = `${data.piece_progress_pct}%`;
}

/**
 * Handle state when all cartons and pieces are loaded
 */
function handleOrderCompleted(data) {
    const gatePassBtn = document.getElementById("gatePassActionBtn");
    const completeBanner = document.getElementById("orderCompleteCelebration");

    if (gatePassBtn) {
        gatePassBtn.classList.remove("hidden");
        const orderId = document.getElementById("orderSelect").value;
        gatePassBtn.href = `/orders/${orderId}/gate-pass`;
    }

    if (completeBanner) {
        completeBanner.classList.remove("hidden");
    }
}

/**
 * Start Camera Scanner
 */
async function startScanner() {
    initAudio();
    const qrRegion = document.getElementById("qr-reader");
    if (!qrRegion) return;

    if (html5QrCode) {
        try {
            await html5QrCode.stop();
        } catch (e) {}
    }

    html5QrCode = new Html5Qrcode("qr-reader");

    const config = {
        fps: 15,
        qrbox: (viewfinderWidth, viewfinderHeight) => {
            const minEdge = Math.min(viewfinderWidth, viewfinderHeight);
            const edge = Math.floor(minEdge * 0.72);
            return { width: edge, height: edge };
        },
        aspectRatio: 1.0,
    };

    try {
        await html5QrCode.start(
            { facingMode: currentCameraFacing },
            config,
            (decodedText, decodedResult) => {
                verifyScannedCode(decodedText);
            },
            (errorMessage) => {
                // frame parsing, safe to ignore
            }
        );

        isScanning = true;
        updateCameraControls(true);
    } catch (err) {
        console.error("Camera access failed:", err);
        alert("Camera permission denied or camera not found. Please allow camera permissions in your browser or use the manual input box.");
        updateCameraControls(false);
    }
}

/**
 * Stop Camera Scanner
 */
async function stopScanner() {
    if (html5QrCode && isScanning) {
        try {
            await html5QrCode.stop();
            isScanning = false;
            updateCameraControls(false);
        } catch (e) {
            console.error("Failed to stop scanner:", e);
        }
    }
}

/**
 * Switch camera front / back
 */
async function toggleCameraFlip() {
    currentCameraFacing = (currentCameraFacing === "environment") ? "user" : "environment";
    if (isScanning) {
        await stopScanner();
        await startScanner();
    }
}

/**
 * Toggle Torch / Flashlight if supported
 */
async function toggleTorch() {
    if (!html5QrCode || !isScanning) return;
    try {
        isTorchOn = !isTorchOn;
        await html5QrCode.applyVideoConstraints({
            advanced: [{ torch: isTorchOn }]
        });
        const torchBtn = document.getElementById("torchToggleBtn");
        if (torchBtn) {
            torchBtn.classList.toggle("bg-yellow-400", isTorchOn);
            torchBtn.classList.toggle("text-slate-900", isTorchOn);
        }
    } catch (err) {
        console.warn("Torch not supported on this device/camera:", err);
    }
}

function updateCameraControls(running) {
    const startBtn = document.getElementById("startCameraBtn");
    const stopBtn = document.getElementById("stopCameraBtn");
    const statusDot = document.getElementById("cameraStatusDot");
    const statusText = document.getElementById("cameraStatusText");

    if (startBtn) startBtn.classList.toggle("hidden", running);
    if (stopBtn) stopBtn.classList.toggle("hidden", !running);

    if (statusDot) {
        statusDot.classList.toggle("bg-emerald-500", running);
        statusDot.classList.toggle("bg-gray-400", !running);
    }
    if (statusText) {
        statusText.textContent = running ? "Camera Active (15 FPS)" : "Camera Offline";
    }
}

// Setup Event Listeners on DOM Ready
document.addEventListener("DOMContentLoaded", () => {
    // Dismiss error modal
    const dismissBtn = document.getElementById("dismissModalBtn");
    if (dismissBtn) dismissBtn.addEventListener("click", dismissErrorModal);

    const startBtn = document.getElementById("startCameraBtn");
    if (startBtn) startBtn.addEventListener("click", startScanner);

    const stopBtn = document.getElementById("stopCameraBtn");
    if (stopBtn) stopBtn.addEventListener("click", stopScanner);

    const flipBtn = document.getElementById("flipCameraBtn");
    if (flipBtn) flipBtn.addEventListener("click", toggleCameraFlip);

    const torchBtn = document.getElementById("torchToggleBtn");
    if (torchBtn) torchBtn.addEventListener("click", toggleTorch);

    // Manual input fallback handler
    const manualForm = document.getElementById("manualScanForm");
    const manualInput = document.getElementById("manualPayloadInput");
    if (manualForm && manualInput) {
        manualForm.addEventListener("submit", (e) => {
            e.preventDefault();
            const val = manualInput.value.trim();
            if (val) {
                verifyScannedCode(val);
                manualInput.value = "";
            }
        });
    }

    // Auto-select order if query param exists
    const urlParams = new URLSearchParams(window.location.search);
    const orderIdParam = urlParams.get("order_id");
    const orderSelect = document.getElementById("orderSelect");
    if (orderSelect && orderIdParam) {
        orderSelect.value = orderIdParam;
        orderSelect.dispatchEvent(new Event("change"));
    }

    // When order changes, reload URL with parameter
    if (orderSelect) {
        orderSelect.addEventListener("change", (e) => {
            const val = e.target.value;
            if (val) {
                window.location.href = `/scanner?order_id=${val}`;
            }
        });
    }

    // Auto-start camera if order is preselected
    if (orderIdParam) {
        // Small delay to allow user interaction gesture
        setTimeout(() => {
            startScanner();
        }, 500);
    }
});
