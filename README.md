# FluxDeluxe (Quick Run)

These steps are for running the desktop app on another machine.

1) Clone the repo with submodules:
   `git clone --recurse-submodules <repo-url>`

2) Create the backend environment (recommended):
   `conda env create -f FluxDeluxe/DynamoDeluxe/Dynamo3.11.yml`

3) Install UI deps:
   `pip install -r requirements.txt`

4) Run the app from the repo root:
   `python -m FluxDeluxe.main`

Notes:
- If you already cloned without submodules, run:
  `git submodule update --init --recursive`
