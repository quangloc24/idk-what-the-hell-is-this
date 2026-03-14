"""
main.py — FastAPI sidecar for hcaptcha-challenger
Exposes a single POST /solve endpoint that returns an hCaptcha token.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
PORT = int(os.getenv("PORT", "7860"))
DEFAULT_PROXY = os.getenv("PROXY", "") or None  # used when /solve request has no proxy


# ── Request / Response models ─────────────────────────────────────────────────

class SolveRequest(BaseModel):
    siteurl: str = "https://owobot.com/captcha"
    sitekey: str = "a6a1d5ce-612d-472d-8e37-7601408fbc09"
    proxy: Optional[str] = None  # e.g. "user:pass@host:port"


class SolveResponse(BaseModel):
    token: str


class HealthResponse(BaseModel):
    status: str
    gemini_configured: bool


# ── App setup ─────────────────────────────────────────────────────────────────

from wireproxy_manager import WireproxyManager

vpn_manager: Optional[WireproxyManager] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global DEFAULT_PROXY
    global vpn_manager
    
    vpn_conf = os.getenv("VPN_CONF", "")
    if vpn_conf:
        logger.info(f"VPN config detected at {vpn_conf}. Starting Wireproxy tunnel...")
        vpn_manager = WireproxyManager(vpn_conf)
        try:
            proxy_url = await vpn_manager.start()
            logger.info(f"Wireproxy started successfully. Routing via {proxy_url}.")
            DEFAULT_PROXY = proxy_url  # Override the default proxy for all requests
        except Exception as e:
            logger.error(f"Failed to start Wireproxy: {e}")
            raise

    if not GEMINI_API_KEY:
        logger.warning(
            "GEMINI_API_KEY is not set! Set it in .env — hcaptcha-challenger will fail."
        )
    else:
        logger.info("✅ GEMINI_API_KEY loaded.")
        
    yield
    
    if vpn_manager:
        logger.info("Shutting down Wireproxy...")
        vpn_manager.stop()


app = FastAPI(
    title="hCaptcha Solver Sidecar",
    description="Solves hCaptcha using TrueDriver (CF bypass) + hcaptcha-challenger (AI vision)",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        gemini_configured=bool(GEMINI_API_KEY),
    )


@app.post("/solve", response_model=SolveResponse)
async def solve(req: SolveRequest):
    """
    Launches Chrome (TrueDriver), navigates to `siteurl`, bridges Playwright
    over CDP, and uses hcaptcha-challenger to solve the hCaptcha.
    Returns the raw h-captcha-response token for submission.
    """
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY not configured. Set it in hcaptcha-service/.env",
        )

    logger.info(f"Solve request: siteurl={req.siteurl} sitekey={req.sitekey[:12]}...")

    try:
        from solver import solve_hcaptcha  # local import to avoid startup issues

        token = await solve_hcaptcha(
            siteurl=req.siteurl,
            sitekey=req.sitekey,
            gemini_api_key=GEMINI_API_KEY,
            proxy=req.proxy or DEFAULT_PROXY,
        )
        return SolveResponse(token=token)

    except TimeoutError:
        logger.error("Solver timed out")
        raise HTTPException(status_code=504, detail="Solver timed out")
    except Exception as e:
        logger.exception("Solver failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
