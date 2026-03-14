import os
import sys
import subprocess
import tempfile
import logging
import asyncio

logger = logging.getLogger(__name__)

class WireproxyManager:
    def __init__(self, conf_path: str, bind_port: int = 1080):
        self.conf_path = conf_path
        self.bind_port = bind_port
        self.process = None
        self.temp_conf_path = None

    async def start(self) -> str:
        if not os.path.exists(self.conf_path):
            raise FileNotFoundError(f"WireGuard config not found at '{self.conf_path}'")

        # Check for wireproxy executable (OS-aware)
        is_windows = sys.platform.startswith("win")
        executable = os.path.join("vpn", "wireproxy.exe" if is_windows else "wireproxy")
        
        if not os.path.exists(executable):
            raise FileNotFoundError(
                f"Wireproxy executable not found at '{executable}'. \n"
                f"Please download it from https://github.com/pufferffish/wireproxy/releases and place it in the 'vpn' folder."
            )

        # Read original WireGuard config 
        with open(self.conf_path, "r", encoding="utf-8") as f:
            config_content = f.read()

        # Add Socks5 section if the config doesn't already have it
        if "[Socks5]" not in config_content:
            config_content += f"\n\n[Socks5]\nBindAddress = 127.0.0.1:{self.bind_port}\n"

        # Write to a secure temporary file so wireproxy can read it
        fd, self.temp_conf_path = tempfile.mkstemp(suffix=".conf")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(config_content)

        logger.info(f"Starting background Wireproxy on port {self.bind_port}...")
        
        self.process = subprocess.Popen(
            [executable, "-c", self.temp_conf_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # Wait a bit for the tunnel to establish
        await asyncio.sleep(4)
        
        if self.process.poll() is not None:
            # Capture any error output from wireproxy for debugging
            try:
                stdout, stderr = self.process.communicate(timeout=1)
                logger.error(f"Wireproxy stdout: {stdout.decode() if stdout else ''}")
                logger.error(f"Wireproxy stderr: {stderr.decode() if stderr else ''}")
            except Exception:
                pass
            raise RuntimeError("Wireproxy failed to start or crashed immediately. Check your VPN config.")


        return f"socks5://127.0.0.1:{self.bind_port}"

    def stop(self):
        if self.process:
            logger.info("Stopping background Wireproxy...")
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        
        if self.temp_conf_path and os.path.exists(self.temp_conf_path):
            try:
                os.remove(self.temp_conf_path)
            except Exception:
                pass

