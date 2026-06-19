import asyncio
from main import build_graph, get_journey

async def run():
    await build_graph()
    res = await get_journey(39.50106, -0.42032, 39.46994, -0.37827)
    import json
    print(json.dumps(res, indent=2))

asyncio.run(run())
