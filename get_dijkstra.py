import asyncio
from main import build_graph, find_path, get_stops_within

async def test():
    await build_graph()
    
    def check(orig_lat, orig_lng, name):
        print(f"\n--- {name} ---")
        orig_stops = get_stops_within(orig_lat, orig_lng, 600)
        dest_stops = get_stops_within(39.46738, -0.37736, 600)
        paths = find_path(orig_stops, dest_stops)
        print(f"Found {len(paths)} static paths.")
        for p in paths:
            print(f"Path weight: {p[0]:.1f}, last line: {p[4]}")
            
    check(39.50068, -0.42074, "Server Coords")
    check(39.50072, -0.42072, "Local Coords")
    
asyncio.run(test())
