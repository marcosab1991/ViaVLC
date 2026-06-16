import json

with open("metro_routes.json", "r") as f:
    data = json.load(f)

for elem in data.get("elements", []):
    if elem["type"] == "relation":
        tags = elem.get("tags", {})
        if tags.get("ref") == "4":
            print(f"Rel: {elem['id']} Name: {tags.get('name')}")
