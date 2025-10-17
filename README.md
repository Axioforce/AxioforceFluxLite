AxioforceFluxLite
=================================

Quick Start
-----------
- **What it is**: A lightweight PySide6 desktop app that connects to a DynamoPy Socket.IO stream and visualizes and live tests single force plates and pitching mounds with live COP and |Fz|.
- **First run**:
  1) Install Python 3.10+ and create/activate a virtual environment.
  2) Install dependencies: `pip install -r requirements.txt`
  3) Start your DynamoPy Socket.IO server (default `http://localhost:3000`).
  4) Optional: set `SOCKET_PORT` to match your server (PowerShell: `$Env:SOCKET_PORT=3000`).
  5) Run the dev-testing branch of DynamoPy.
  6) Launch: `python -m src.main`

