"""Shared outbound-HTTP helper: 15 s timeout, exponential-backoff retries."""
import asyncio
import logging

import httpx

from .config import HTTP_TIMEOUT, HTTP_RETRIES

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 502, 503, 504}


async def request_with_retry(client: httpx.AsyncClient, method: str, url: str,
                             retries: int = HTTP_RETRIES, **kwargs) -> httpx.Response:
    """Perform a request, retrying transient failures with 1s/2s/4s backoff."""
    attempt = 0
    while True:
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code in RETRYABLE_STATUS and attempt < retries:
                raise httpx.HTTPStatusError("retryable status", request=resp.request, response=resp)
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if attempt >= retries:
                raise
            delay = 2 ** attempt
            attempt += 1
            log.warning("HTTP %s %s failed (%s); retry %d/%d in %ds",
                        method, url, exc.__class__.__name__, attempt, retries, delay)
            await asyncio.sleep(delay)


def make_client(verify_tls: bool, auth=None, headers=None) -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=verify_tls, auth=auth, headers=headers or {},
                             timeout=HTTP_TIMEOUT, follow_redirects=True)
