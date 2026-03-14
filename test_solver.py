import asyncio
import os
import time
import logging
from dotenv import load_dotenv
from solver import solve_hcaptcha
from wireproxy_manager import WireproxyManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
)

load_dotenv()

async def main():
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        print("ERROR: GEMINI_API_KEY is not set in .env")
        return
        
    proxy = os.getenv("PROXY")
    vpn_conf = os.getenv("VPN_CONF")
    vpn_manager = None

    if vpn_conf and os.path.exists(vpn_conf):
        print(f"Starting Wireproxy from {vpn_conf}...")
        vpn_manager = WireproxyManager(vpn_conf)
        proxy = await vpn_manager.start()
        print(f"Wireproxy started at {proxy}")

    sitekey = "a6a1d5ce-612d-472d-8e37-7601408fbc09" # OwO bot default sitekey
    siteurl = "https://owobot.com/captcha"
    
    print(f"Testing solver...")
    print(f"Sitekey: {sitekey}")
    print(f"Proxy: {proxy or 'None'}")
    print("-" * 50)
    
    start_time = time.time()
    try:
        token = await solve_hcaptcha(
            siteurl=siteurl,
            sitekey=sitekey,
            gemini_api_key=gemini_key,
            proxy=proxy
        )
        elapsed = time.time() - start_time
        print(f"\n✅ SUCCESS! Token obtained in {elapsed:.2f} seconds.")
        print("-" * 50)
        print(token)
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n❌ FAILED after {elapsed:.2f} seconds:")
        print(e)
        import traceback
        traceback.print_exc()
    finally:
        if vpn_manager:
            vpn_manager.stop()

if __name__ == "__main__":
    asyncio.run(main())
