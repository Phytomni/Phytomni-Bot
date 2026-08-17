# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Bot-side V1 conversation-context store and turn service.

Always on in this process. The Go gateway decides whether to send a
V1 envelope (``bot.multiturn_v1_enabled``). Wire-field identities live
in :mod:`mcp_server_phytomni.contracts.conversation_context`.
"""

from .models import ConversationEnvelopeV1
from .service import (
    AsyncAcceptanceError,
    AsyncAgentAcceptance,
    ContextStoreUnavailableError,
)

__all__ = [
    "AsyncAcceptanceError",
    "AsyncAgentAcceptance",
    "ConversationEnvelopeV1",
    "ContextStoreUnavailableError",
]
