"""Server settings, from environment variables (all optional)."""
import os

MOCK_MODELS = os.environ.get("MOCK_MODELS", "0") == "1"        # serve recorded model responses, no Meta calls
MOCK_DELAY = float(os.environ.get("MOCK_DELAY", "1.0"))          # seconds per mocked model call, to feel like streaming
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".server-data"))
RUNS_DIR = os.path.join(DATA_DIR, "runs")
SPEND_LOG = os.path.join(DATA_DIR, "spend.jsonl")               # every production API call, one JSON line each
RUNS_LOG = os.path.join(DATA_DIR, "runs.jsonl")                 # one line per run: read time and tokens, latency
PRESET = os.environ.get("TALOS_PRESET", "live")

DAILY_CAP_USD = float(os.environ.get("DAILY_CAP_USD", "5.0"))   # global, all users, per UTC day
RUN_CAP_USD = float(os.environ.get("RUN_CAP_USD", "0.40"))      # one run never spends more than this
RUNS_PER_IP_PER_HOUR = int(os.environ.get("RUNS_PER_IP_PER_HOUR", "5"))
COMPILES_PER_IP_PER_SECOND = float(os.environ.get("COMPILES_PER_IP_PER_SECOND", "8"))
EDITS_PER_IP_PER_HOUR = int(os.environ.get("EDITS_PER_IP_PER_HOUR", "30"))   # each edit is one model call (~$0.005)
EDIT_CAP_USD = float(os.environ.get("EDIT_CAP_USD", "0.05"))     # one edit (two calls at most) never spends more
MAX_INSTRUCTION_CHARS = 300
MAX_AUDIO_BYTES = 2 * 1024 * 1024                                # 30 s of 16 kHz mono 16-bit WAV is ~1 MB
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 2000
MAX_PROMPT_CHARS = 1000
RETENTION_HOURS = 24
