# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
NAT plugin registration for unified AI-Q API.

Combines Knowledge API (collections/documents) and Async Job API (agent jobs/SSE streaming).

Knowledge Layer Configuration:
    The Knowledge API uses the same ingestor instance as the knowledge_retrieval tool.
    Configure the backend via the knowledge_retrieval function in your workflow YAML:

    functions:
      knowledge_search:
        _type: knowledge_retrieval
        backend: foundational_rag
        rag_url: http://localhost:8081/v1
        ingest_url: http://localhost:8082/v1

    The API plugin will automatically use the configured backend. If no tool is
    configured, it falls back to environment variables (KNOWLEDGE_INGESTOR_BACKEND)
    or the default backend (llamaindex).
"""

import logging
import os
import signal
from collections.abc import Callable
from typing import override

from fastapi import APIRouter
from fastapi import FastAPI
from pydantic import Field

from nat.builder.workflow_builder import WorkflowBuilder
from nat.cli.register_workflow import register_front_end
from nat.data_models.config import Config
from nat.front_ends.fastapi.fastapi_front_end_config import FastApiFrontEndConfig
from nat.front_ends.fastapi.fastapi_front_end_plugin import FastApiFrontEndPlugin
from nat.front_ends.fastapi.fastapi_front_end_plugin_worker import FastApiFrontEndPluginWorker
from nat.front_ends.fastapi.fastapi_front_end_plugin_worker import FastApiFrontEndPluginWorkerBase

from .chat.routes import configure as configure_chat
from .chat.routes import router as chat_router
from .jobs import EventStore
from .jobs import get_connection_manager
from .routes.collections import add_collection_routes
from .routes.documents import add_document_routes
from .routes.jobs import register_job_routes
from .sessions.routes import router as sessions_router
from .sessions.routes import set_store as set_session_store
from .websocket_reconnect import install_reconnectable_handler

logger = logging.getLogger(__name__)

install_reconnectable_handler()


class AIQAPIConfig(FastApiFrontEndConfig, name="aiq_api"):
    """
    Configuration for unified AI-Q API endpoints.

    Knowledge API:
        Automatically enabled when a knowledge_retrieval function is configured.
        Backend settings are inherited from that function's config.

    Async Job API:
        Configure db_url and expiry_seconds for job persistence.
    """

    db_url: str = Field(
        default="sqlite+aiosqlite:///./jobs.db",
        description="Database URL for job store and event store",
    )
    expiry_seconds: int = Field(
        default=86400,
        ge=600,
        le=604800,
        description="Job expiry time in seconds (default: 24 hours)",
    )


# Track if shutdown signal has been received (for force exit on second Ctrl+C)
_shutdown_signal_received = False


def _create_shutdown_signal_handler(
    original_handler: Callable | signal.Handlers | None,
    sig: signal.Signals,
) -> Callable:
    """
    Create a signal handler that signals SSE shutdown before calling the original handler.

    This ensures SSE connections are notified of shutdown before uvicorn cancels tasks.
    On second signal, force exits immediately.
    """

    def handler(signum, frame):
        global _shutdown_signal_received

        if _shutdown_signal_received:
            logger.warning("Second %s received, forcing exit...", sig.name)
            os._exit(1)

        _shutdown_signal_received = True
        logger.info("Signal %s received, signaling SSE shutdown... (press again to force quit)", sig.name)
        connection_manager = get_connection_manager()

        connection_manager.signal_shutdown()

        if original_handler and callable(original_handler):
            original_handler(signum, frame)
        elif original_handler == signal.SIG_DFL:
            signal.signal(sig, signal.SIG_DFL)
            signal.raise_signal(sig)

    return handler


class AIQAPIWorker(FastApiFrontEndPluginWorker):
    """
    Worker that adds unified AI-Q API routes to the FastAPI app.

    Combines:
    - Knowledge API routes (collections, documents) - uses factory singleton
    - Async Job API routes (agent jobs, SSE streaming)
    """

    _original_sigint_handler: Callable | signal.Handlers | None = None
    _original_sigterm_handler: Callable | signal.Handlers | None = None

    @override
    def build_app(self) -> FastAPI:
        app = super().build_app()

        app.title = "AI-Q API"
        app.description = "Async research jobs, knowledge management, and agent orchestration."
        app.version = "1.0.0"

        knowledge_router = APIRouter()
        add_collection_routes(knowledge_router)
        add_document_routes(knowledge_router)
        app.include_router(knowledge_router)
        logger.info("Knowledge API routes registered")

        app.include_router(sessions_router)
        logger.info("Session persistence routes registered")

        app.include_router(chat_router)
        logger.info("Chat API routes registered")

        return app

    @override
    async def add_routes(self, app: FastAPI, builder: WorkflowBuilder):
        await super().add_routes(app, builder)

        # =====================================================================
        # Session persistence store
        # =====================================================================
        await self._init_session_store()

        # =====================================================================
        # Chat API (SSE+REST) — inline runner for unified chat
        # =====================================================================
        await self._init_chat_runner()

        # =====================================================================
        # Async Job API routes
        # =====================================================================
        await register_job_routes(app, builder, self)
        logger.info("Async Job API routes registered")

        self._install_signal_handlers()

        @app.on_event("shutdown")
        async def shutdown_sse_connections():
            """Gracefully close all active SSE connections and background tasks on shutdown."""
            logger.info("Shutting down SSE connections...")
            connection_manager = get_connection_manager()
            await connection_manager.shutdown(timeout=5.0)

            from .routes.jobs import stop_periodic_cleanup

            await stop_periodic_cleanup()

            await EventStore.dispose_all_engines_async()
            logger.info("SSE shutdown complete")

            self._restore_signal_handlers()

        try:
            from aiq_debug import register_debug_routes

            await register_debug_routes(app)
            logger.info("Debug console registered at /debug")
        except ImportError:
            pass

    async def _init_session_store(self):
        """Initialize the session persistence store from DB URL."""
        import os

        db_url = os.environ.get("AIQ_SESSIONS_DB") or os.environ.get("NAT_JOB_STORE_DB_URL")
        if not db_url:
            logger.info("No session DB configured (set AIQ_SESSIONS_DB or NAT_JOB_STORE_DB_URL)")
            return

        try:
            from .sessions.store import ConversationStore
            from .sessions.store import get_engine

            engine = await get_engine(db_url)
            store = ConversationStore(engine)
            set_session_store(store)
            logger.info("Session store initialized (db: %s)", db_url[:50])
        except Exception:
            logger.warning("Failed to initialize session store", exc_info=True)

    async def _init_chat_runner(self):
        """Initialize the inline chat runner with SSE event emission."""
        import os

        from .chat.inline_runner import InlineRunner
        from .sessions.routes import _store as conversation_store

        db_url = os.environ.get("AIQ_SESSIONS_DB") or os.environ.get("NAT_JOB_STORE_DB_URL")
        if not db_url:
            logger.info("No DB configured for chat runner — chat API will return 503")
            return

        if not self._session_managers:
            logger.warning("No session manager available — chat runner not initialized")
            return

        try:
            session_manager = self._session_managers[0]
            step_adaptor = self.get_step_adaptor()

            runner = InlineRunner(
                session_manager=session_manager,
                step_adaptor=step_adaptor,
                event_store_db_url=db_url,
                conversation_store=conversation_store,
            )
            configure_chat(runner, conversation_store, db_url)
            logger.info("Chat runner initialized")
        except Exception:
            logger.warning("Failed to initialize chat runner", exc_info=True)

    def _install_signal_handlers(self):
        """Install signal handlers to notify SSE connections on shutdown."""
        try:
            self._original_sigint_handler = signal.getsignal(signal.SIGINT)
            self._original_sigterm_handler = signal.getsignal(signal.SIGTERM)

            signal.signal(
                signal.SIGINT,
                _create_shutdown_signal_handler(self._original_sigint_handler, signal.SIGINT),
            )
            signal.signal(
                signal.SIGTERM,
                _create_shutdown_signal_handler(self._original_sigterm_handler, signal.SIGTERM),
            )
            logger.debug("Installed SSE shutdown signal handlers")
        except Exception as e:
            logger.warning("Failed to install signal handlers: %s", e)

    def _restore_signal_handlers(self):
        """Restore original signal handlers."""
        try:
            if self._original_sigint_handler is not None:
                signal.signal(signal.SIGINT, self._original_sigint_handler)
            if self._original_sigterm_handler is not None:
                signal.signal(signal.SIGTERM, self._original_sigterm_handler)
            logger.debug("Restored original signal handlers")
        except Exception as e:
            logger.warning("Failed to restore signal handlers: %s", e)


class AIQAPIPlugin(FastApiFrontEndPlugin):
    """Plugin that adds unified AI-Q API endpoints to the FastAPI server."""

    def __init__(self, full_config: Config, config: AIQAPIConfig):
        super().__init__(full_config=full_config)
        self.config = config

    @override
    def get_worker_class(self) -> type[FastApiFrontEndPluginWorkerBase]:
        return AIQAPIWorker


@register_front_end(config_type=AIQAPIConfig)
async def register_aiq_api(config: AIQAPIConfig, full_config: Config):
    """Register unified AI-Q API with NAT framework."""
    yield AIQAPIPlugin(full_config=full_config, config=config)
