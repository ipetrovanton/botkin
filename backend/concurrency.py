"""Глобальный семафор для контроля VRAM при VLM-инференсе."""
import asyncio

LLM_SEMAPHORE = asyncio.Semaphore(1)