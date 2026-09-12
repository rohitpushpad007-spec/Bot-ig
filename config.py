import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Collect IG_SESSIONID_1, IG_SESSIONID_2, ... IG_SESSIONID_10 (any that are set)
IG_SESSIONIDS = []
for i in range(1, 11):
    val = os.getenv(f"IG_SESSIONID_{i}", "").strip()
    if val:
        IG_SESSIONIDS.append(val)

# Back-compat: also allow a single IG_SESSIONID
_single = os.getenv("IG_SESSIONID", "").strip()
if _single and _single not in IG_SESSIONIDS:
    IG_SESSIONIDS.append(_single)

_allowed = os.getenv("ALLOWED_USER_ID", "").strip()
ALLOWED_USER_ID = int(_allowed) if _allowed else None

DOWNLOAD_DIR = "downloads"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in .env")
if not IG_SESSIONIDS:
    raise RuntimeError("No IG_SESSIONID_1 / IG_SESSIONID_2 ... found in environment.")
