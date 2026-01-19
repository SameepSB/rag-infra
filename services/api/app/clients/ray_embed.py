# services/api/app/clients/ray_embed.py
import httpx
from services.api.app.config import settings

class RayEmbedClient:
    """
    Client for the Ray Serve Embedding Service.
    Uses HTTPX for async non-blocking HTTP calls.
    """
    async def embed_query(self, text: str) -> list[float]:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.RAY_EMBED_ENDPOINT,
                json={"text": text, "task_type": "query"} # "query" instructs model to optimize for retrieval
            )
            response.raise_for_status()
            return response.json()["embedding"]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Used during ingestion"""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                settings.RAY_EMBED_ENDPOINT,
                json={"text": texts, "task_type": "document"}
            )
            response.raise_for_status()
            return response.json()["embeddings"]

embed_client = RayEmbedClient()