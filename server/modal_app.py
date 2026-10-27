"""Hephaestus backend on Modal.

  modal deploy server/modal_app.py                          # development: scales to zero when idle
  HEPH_MIN_CONTAINERS=1 modal deploy server/modal_app.py    # judging period (Nov 4-9): one container always warm

One container serves everything (max_containers=1): run state lives in memory and a run mostly waits on the Meta
API. If that ever becomes a bottleneck, move run state to a modal.Dict and lift the limit.
Secrets: the Modal secret "meta-model-api" provides MODEL_API_KEY; it never reaches the frontend.
Data: the volume "hephaestus-data" holds runs (deleted after 24 h) and spend.jsonl (every production API call).
  modal volume get hephaestus-data spend.jsonl -          # check production spend
"""
import os

import modal

MIN_CONTAINERS = int(os.environ.get("HEPH_MIN_CONTAINERS", "0"))
SKIP = ["**/__pycache__/**", "**/*.pyc"]

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0", "libxrender1", "libxext6", "libsm6")
    .pip_install("cadquery==2.8.0", "fastapi[standard]==0.162.0", "python-multipart", "pydantic>=2.6", "numpy",
                 "scipy", "pillow", "pillow-heif", "trimesh", "requests", "matplotlib", "meta-sam-parser==0.1.0",
                 "opencv-python-headless")
    .env({"DATA_DIR": "/data", "TALOS_PRESET": "live", "MPLBACKEND": "Agg"})
    .add_local_python_source("hephaestus", "server", ignore=SKIP)   # includes fewshot/ and mock_data/ JSON
)
volume = modal.Volume.from_name("hephaestus-data", create_if_missing=True)
app = modal.App("hephaestus", image=image)


@app.function(secrets=[modal.Secret.from_name("meta-model-api")], volumes={"/data": volume},
              min_containers=MIN_CONTAINERS, max_containers=1, scaledown_window=20 * 60, timeout=60 * 60, cpu=2.0,
              memory=4096)
@modal.concurrent(max_inputs=200)
@modal.asgi_app()
def web():
    import server.api  # noqa: F401 - imports CadQuery at container start

    def persist():
        try:
            volume.commit()
        except Exception as e:  # noqa: BLE001 - persistence is best effort; the next commit catches up
            print("volume commit failed:", e)

    return server.api.create_app(on_finish=persist)
