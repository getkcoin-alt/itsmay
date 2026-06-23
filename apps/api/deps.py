"""FastAPI dependency providers — single-instance services held on app.state."""

from __future__ import annotations

from fastapi import Request

from core.brain.llm import LLMClient
from core.memory.embedder import Embedder
from core.memory.episodic import EpisodicStore
from core.memory.semantic import SemanticStore


def get_llm(req: Request) -> LLMClient:
    return req.app.state.llm


def get_embedder(req: Request) -> Embedder:
    return req.app.state.embedder


def get_episodic(req: Request) -> EpisodicStore:
    return req.app.state.episodic


def get_semantic(req: Request) -> SemanticStore:
    return req.app.state.semantic
