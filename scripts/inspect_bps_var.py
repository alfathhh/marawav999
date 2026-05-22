import asyncio
import json
import sys

from app.config import get_settings
from app.services.bps_client import BpsClient


async def main() -> None:
    settings = get_settings()
    client = BpsClient(settings.bps_api_key, settings.bps_domain)
    var_id = sys.argv[1] if len(sys.argv) > 1 else "279"
    for model in ["th", "turth", "vervar", "turvar"]:
        payload = await client._bps_list(settings.bps_domain, model, var=var_id)
        rows = client._rows(payload)
        print(f"\nMODEL {model} ROWS {len(rows)}")
        print(json.dumps(rows[:10], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
