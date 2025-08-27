"""Simple FastAPI application example for ANP Proxy."""

import time

from fastapi import FastAPI, Request

app = FastAPI(title="Simple FastAPI App", version="1.0.0")


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Hello from ANP Proxy!", "timestamp": time.time()}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "fastapi-example"}


@app.get("/echo/{item}")
async def echo_item(item: str, q: str | None = None):
    """Echo endpoint with path and query parameters."""
    result = {"item": item}
    if q:
        result["query"] = q
    return result


@app.post("/echo")
async def echo_post(request: Request):
    """Echo POST data."""
    body = await request.body()
    return {
        "method": request.method,
        "url": str(request.url),
        "headers": dict(request.headers),
        "body": body.decode() if body else None,
        "timestamp": time.time(),
    }


@app.get("/large-response")
async def large_response():
    """Generate a large response to test chunking."""
    data = {"numbers": list(range(10000))}
    return data


@app.get("/slow-response")
async def slow_response():
    """Simulate slow response."""
    import asyncio

    await asyncio.sleep(2)
    return {"message": "This response took 2 seconds", "timestamp": time.time()}


# ANP Proxy 1 and 2 endpoints
@app.post("/anpproxy1")
async def anpproxy1_endpoint(request: Request):
    """ANP Proxy 1 endpoint for POST processing."""
    body = await request.body()
    return {
        "service": "anpproxy1_agent",
        "message": "ANP Proxy 1 POST processing response",
        "method": request.method,
        "url": str(request.url),
        "body": body.decode() if body else None,
        "timestamp": time.time(),
    }


@app.get("/anpproxy2")
async def anpproxy2_endpoint(q: str | None = None):
    """ANP Proxy 2 endpoint for GET query processing."""
    return {
        "service": "anpproxy2_agent",
        "message": "ANP Proxy 2 GET query response",
        "query": q,
        "timestamp": time.time(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
