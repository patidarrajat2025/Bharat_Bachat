"""MongoDB transaction helpers for multi-document financial operations.

MongoDB transactions require a replica set or sharded cluster. Callers must pass
`session` to every MongoDB operation in the callback; otherwise those operations
are not part of the transaction. Do not use this helper for external side effects
such as notifications; persist an outbox/pending marker and recover it separately.
"""
from typing import Awaitable, Callable, TypeVar
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.client_session import ClientSession

T = TypeVar("T")


async def run_in_transaction(client: AsyncIOMotorClient, operation: Callable[[ClientSession], Awaitable[T]]) -> T:
    """Execute a database-only callback atomically, with MongoDB retry handling."""
    async with await client.start_session() as session:
        async def callback(active_session: ClientSession) -> T:
            return await operation(active_session)
        return await session.with_transaction(callback)
