from .config import BridgeConfig
from .events import ShoreEventStream
from .identity import BridgeIdentity, SessionBucketStore
from .prompting import build_recall_block, build_recall_preview, format_agent_state
from .writeback import BackgroundWriteback, PendingTurn, ResponseDeduper

try:
    from .client import ShoreClient, ShoreRequestError
except ModuleNotFoundError as exc:
    if exc.name != "httpx":
        raise
    ShoreClient = None
    ShoreRequestError = None

__all__ = [
    "BackgroundWriteback",
    "BridgeConfig",
    "BridgeIdentity",
    "PendingTurn",
    "ResponseDeduper",
    "SessionBucketStore",
    "ShoreClient",
    "ShoreEventStream",
    "ShoreRequestError",
    "build_recall_block",
    "build_recall_preview",
    "format_agent_state",
]
