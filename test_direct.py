import asyncio
from main import get_line_geometry

async def test():
    data = await get_line_geometry("4", "metro", "")
    print("Success:", data.get("success"))
    print("Has geometry:", "geometry" in data and data["geometry"] is not None)
    if data.get("geometry"):
        print("Type:", data["geometry"].get("type"))
        print("Coords len:", len(data["geometry"].get("coordinates", [])))

asyncio.run(test())
