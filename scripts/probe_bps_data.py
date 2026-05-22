import asyncio
import json
import sys

from app.config import get_settings
from app.services.bps_client import BpsClient


async def main() -> None:
    settings = get_settings()
    client = BpsClient(settings.bps_api_key, settings.bps_domain)
    var_id = sys.argv[1] if len(sys.argv) > 1 else "279"
    year_id = sys.argv[2] if len(sys.argv) > 2 else "123"

    for params in [
        {"var": var_id, "th": year_id, "turth": "0", "vervar": "0", "turvar": "0"},
        {"var": var_id, "th": year_id, "turth": "0", "vervar": "1", "turvar": "0"},
        {"var": var_id, "th": year_id, "turth": "0", "vervar": "2", "turvar": "0"},
        {"var": var_id, "th": year_id, "turth": "0", "vervar": "3", "turvar": "0"},
        {"var": var_id, "th": year_id, "turth": "0", "vervar": "4", "turvar": "0"},
        {"var": var_id, "th": year_id, "turth": "0", "vervar": "5", "turvar": "0"},
        {"var": var_id, "th": year_id, "turth": "0", "vervar": "542", "turvar": "0"},
        {"var": var_id, "th": year_id, "turth": "0", "vervar": "543", "turvar": "0"},
        {"var": var_id, "th": year_id, "turth": "0", "vervar": "544", "turvar": "0"},
        {"var": var_id, "th": year_id, "turth": "0", "vervar": "545", "turvar": "0"},
        {"var": var_id, "th": year_id, "turth": "0", "vervar": "546", "turvar": "0"},
    ]:
        try:
            payload = await client._fetch_variable_data(settings.bps_domain, params)
        except Exception as exc:
            print("ERROR", params, type(exc).__name__, exc)
            continue
        values = client._collect_values(payload)
        print("PARAMS", params, "VALUES", values[:10], "RAW_KEYS", list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__)
        if values:
            print(json.dumps(payload, ensure_ascii=False)[:500])


if __name__ == "__main__":
    asyncio.run(main())
