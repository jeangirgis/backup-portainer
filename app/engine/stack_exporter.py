import httpx
import os
from pathlib import Path
from app.exceptions import PortainerAuthError, PortainerStackNotFoundError, PortainerConnectionError

class StackExporter:
    def __init__(self, portainer_url: str, api_token: str, ssl_verify: bool = True):
        self.base_url = portainer_url.rstrip("/")
        self.headers = {"X-API-Key": api_token}
        self.ssl_verify = ssl_verify

    async def export(self, stack_id: str, output_dir: Path):
        stack_dir = output_dir / "stack"
        stack_dir.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(timeout=30.0, verify=(str(self.ssl_verify).lower() == "true")) as client:
            try:
                # 1. Get Stack Details
                resp = await client.get(f"{self.base_url}/api/stacks/{stack_id}", headers=self.headers)
                if resp.status_code == 401:
                    raise PortainerAuthError("Check PORTAINER_API_TOKEN")
                if resp.status_code == 404:
                    raise PortainerStackNotFoundError(f"Stack {stack_id} not found")
                resp.raise_for_status()
                stack_data = resp.json()

                # 2. Get Compose File
                resp = await client.get(f"{self.base_url}/api/stacks/{stack_id}/file", headers=self.headers)
                resp.raise_for_status()
                compose_content = resp.json().get("StackFileContent", "")

                with open(stack_dir / "docker-compose.yml", "w", encoding="utf-8") as f:
                    f.write(compose_content)

                # 3. Save Environment Variables
                env_vars = stack_data.get("Env", [])
                with open(stack_dir / "stack.env", "w", encoding="utf-8") as f:
                    for env in env_vars:
                        f.write(f"{env['name']}={env['value']}\n")
                
                return stack_data

            except httpx.RequestError as exc:
                raise PortainerConnectionError(f"Failed to connect to Portainer at {self.base_url}: {exc}")
