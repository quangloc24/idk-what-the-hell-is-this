"""
solver.py — hcaptcha-challenger via Playwright
Loads hCaptcha's own demo page with the target sitekey (same as 2captcha workers),
solves with Gemini AI, and returns the signed token.

Why this works without logging into owobot.com:
  - hCaptcha tokens are signed by hCaptcha's servers and bound only to the sitekey+host pair.
  - owobot.com's /api/captcha/verify just calls hcaptcha.com/siteverify to validate the token.
  - The TypeScript side's axios session handles the owobot.com cookie — the solver is stateless.
"""

import asyncio
import logging
import os
import sys
from typing import Optional

from camoufox import AsyncCamoufox
from browserforge.fingerprints import Screen
from hcaptcha_challenger.agent import AgentV, AgentConfig
from hcaptcha_challenger.models import CaptchaResponse
from hcaptcha_challenger import types

logger = logging.getLogger(__name__)

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
SOLVE_TIMEOUT = int(os.getenv("SOLVE_TIMEOUT", "120"))  # seconds

def _build_challenge_url(sitekey: str, siteurl: str) -> str:
    """Builds the hCaptcha demo page URL."""
    return f"https://accounts.hcaptcha.com/demo?sitekey={sitekey}"

import httpx

async def _check_proxy(proxy_str: str) -> bool:
    """Quickly check if the proxy can connect to hCaptcha before starting a whole browser."""
    if "://" not in proxy_str:
        proxy_url = f"http://{proxy_str}"
    else:
        proxy_url = proxy_str

    try:
        async with httpx.AsyncClient(proxy=proxy_url, verify=False, timeout=10.0) as client:
            resp = await client.get("https://accounts.hcaptcha.com/demo")
            if resp.status_code == 200:
                return True
            else:
                logger.warning(f"Proxy returned status {resp.status_code}. Likely blocked.")
                return False
    except Exception as e:
        logger.warning(f"Proxy connection failed: {e}")
        return False

async def solve_hcaptcha(
    siteurl: str,
    sitekey: str,
    gemini_api_key: str,
    proxy: Optional[str] = None,
) -> str:
    """
    Solves hCaptcha using Camoufox (Firefox-based stealth browser) + Gemini AI.
    """
    os.environ["GEMINI_API_KEY"] = gemini_api_key

    challenge_url = _build_challenge_url(sitekey, siteurl)
    logger.info(f"Loading hCaptcha challenge: {challenge_url}")

    if proxy:
        logger.info(f"Testing proxy {proxy} before launching browser...")
        is_working = await _check_proxy(proxy)
        if not is_working:
            logger.warning("Proxy failed health check. Falling back to local IP.")
            proxy = None
        else:
            logger.info("Proxy is working fine.")

    # Camoufox (via Playwright) expects a dict for proxy: {"server": "...", "username": "...", "password": "..."}
    pw_proxy = None
    if proxy:
        scheme = "http"
        work_proxy = proxy
        if "://" in work_proxy:
            scheme, work_proxy = work_proxy.split("://", 1)

        if "@" in work_proxy:
            creds, server = work_proxy.rsplit("@", 1)
            if ":" in creds:
                username, password = creds.split(":", 1)
                pw_proxy = {"server": f"{scheme}://{server}", "username": username, "password": password}
            else:
                pw_proxy = {"server": f"{scheme}://{server}", "username": creds}
        else:
            pw_proxy = {"server": f"{scheme}://{work_proxy}"}

    async with AsyncCamoufox(
        headless=HEADLESS, # Temporarily keeping False as user requested for debugging
        proxy=pw_proxy,
        screen=Screen(max_width=1920, max_height=1080),
        humanize=False,
    ) as browser:
        page = await browser.new_page()

        try:
            try:
                # Use a slightly more relaxed wait as Camoufox handles things differently
                response = await page.goto(challenge_url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as e:
                raise RuntimeError(f"Could not reach hCaptcha demo page. Error: {e}")
                
            if response and not response.ok:
                raise RuntimeError(f"hCaptcha page blocked access with status {response.status}. Proxy or IP might be blocked.")

            logger.info("Challenge page loaded, initialising AgentV...")
            agent_config = AgentConfig(
                DISABLE_BEZIER_TRAJECTORY=True,
                ignore_request_types=[
                    types.ChallengeTypeEnum.IMAGE_LABEL_MULTI_SELECT,
                    #types.ChallengeTypeEnum.IMAGE_DRAG_MULTI
                ],
                ignore_request_questions=[
                    "Drag each segment to its position on the line", 
                    "Select all animals that do not have legs"
                ],
                GEMINI_API_KEY=gemini_api_key,
                #IMAGE_CLASSIFIER_MODEL='gemini-3.1-flash-lite-preview',
                SPATIAL_PATH_REASONER_MODEL='gemini-3.1-flash-lite-preview',
                SPATIAL_POINT_REASONER_MODEL='gemini-3.1-flash-lite-preview',
            )
            agent = AgentV(page=page, agent_config=agent_config)

            # Important: Give page time to breathe
            # logger.info("Waiting for page to load...")
            # await page.wait_for_timeout(10000)

            # Trigger the challenge by clicking the checkbox
            # logger.info("Clicking checkbox...")
            # await agent.robotic_arm.click_checkbox()

            # logger.info("Waiting for captcha widget to fully load...")
            # await page.wait_for_timeout(10000)

            # Wait for AI to solve all image rounds
            logger.info("Solving challenge (Gemini AI)...")
            await asyncio.wait_for(agent.wait_for_challenge(), timeout=SOLVE_TIMEOUT)

            # Extract token
            token: Optional[str] = None
            if agent.cr_list:
                cr: CaptchaResponse = agent.cr_list[-1]
                cr_dict = cr.model_dump(by_alias=True)
                token = cr_dict.get("generated_pass_UUID")
                if token:
                    logger.info("Token cleanly extracted from CaptchaResponse object!")

            if not token:
                logger.info("Falling back to DOM extraction...")
                token = await page.evaluate(
                    "() => document.querySelector('[name=\"h-captcha-response\"]')?.value"
                )

            if not token:
                raise RuntimeError("Challenge solved but no token found in page.")

            logger.info(f"✅ Token obtained ({len(token)} chars)")
            return token

        finally:
            await page.close()

