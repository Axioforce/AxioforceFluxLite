AxioforceFluxLite
=================================

Overview
--------
This Python GUI connects to the Axioforce DynamoPy Socket.IO stream and renders an overhead schematic of a pitching mound with three force plates: the Launch Zone (type 07) and two Landing Zone plates (type 08). It displays COP (center of pressure) markers for the Launch Zone and the aggregate Landing Zone virtual device, scaling the COP marker radius based on the total vertical force |Fz|.

Features
--------
- Auto-connect to Socket.IO on startup (default port 3000; override via env `SOCKET_PORT` or UI)
- Reconnect with backoff; connection status shown in the status bar
- Listens to `jsonData` events and filters to the "Launch Zone" and aggregate "Landing Zone" virtual devices
- COP marker radius scales by |Fz| with a UI-adjustable scale factor
- Light smoothing (EWMA) on COP and |Fz|
- Approx 60 FPS rendering
- Qt (PySide6) UI (Tkinter support removed)

Install
-------
1. Create/activate a Python 3.10+ environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

Run
---
Set an optional port override and start the app:

```bash
# optional
set SOCKET_PORT=3000  # Windows PowerShell: $Env:SOCKET_PORT=3000

python -m src.main
```

Controls
--------
- Host/Port override and Connect/Disconnect
- COP Scale slider to tune COP radius scaling `r_px = clamp(r_min, k * |Fz|, r_max)`
- Show/hide plates and markers

Notes on Identification
-----------------------
The app uses position semantics. The live `jsonData` payloads do not include `position_id`, so the app parses the `device_id` suffix to detect the virtual devices named "Launch Zone" and "Landing Zone" (aggregate). The parsing is case-insensitive and tolerant of separators (spaces, hyphens, underscores).

Plate Layout (mm)
-----------------
- Launch Zone (type 07) centered at (0, 0), footprint ≈ 266.06 × 570.86
- Landing Zone midpoint at y = +914.4
- Upper Landing Zone plate center y = 914.4 + 260
- Lower Landing Zone plate center y = 914.4 - 260
- Landing Zone (type 08) footprint ≈ 520.06 × 570.86

Tuning
------
- **COP scale factor (k)**: Adjust using the slider. Start around `0.01` px/N. Depending on athlete mass and impacts, increase or decrease to taste.
- **Noise threshold**: |Fz| values below ~22 N are ignored to suppress noise.

Troubleshooting
---------------
- Ensure DynamoPy Socket.IO server is running at `http://localhost:<port>`.
- PySide6 is required; there is no Tkinter fallback.
- For high refresh rates, keep the app window focused and visible; background throttling can occur on some platforms.

License
-------
MIT


