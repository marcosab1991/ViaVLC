from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import httpx
import uvicorn
import math
from datetime import datetime

app = FastAPI(title="OTP Router API")

# Allow CORS for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OTP_URL = "http://localhost:8080/otp/routers/default/plan"

@app.get("/api/calcular_rota")
async def calcular_rota(
    origem_lat: float = Query(...),
    origem_lon: float = Query(...),
    destino_lat: float = Query(...),
    destino_lon: float = Query(...)
):
    """
    Endpoint BFF para comunicar com o OpenTripPlanner.
    Recebe as coordenadas e devolve as instruções de viagem limpas.
    """
    # OTP REST API params
    params = {
        "fromPlace": f"{origem_lat},{origem_lon}",
        "toPlace": f"{destino_lat},{destino_lon}",
        "time": "12:00pm",
        "date": "06-30-2026",
        "mode": "TRANSIT,WALK",
        "maxWalkDistance": 2000, # 2km max walk
        "arriveBy": "false"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(OTP_URL, params=params, timeout=15.0)
            
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Erro ao conectar ao OTP")
            
        data = response.json()
        
        # Check for errors in OTP response
        if "error" in data:
            raise HTTPException(status_code=400, detail=data["error"].get("msg", "Erro no roteamento"))
            
        if not data.get("plan", {}).get("itineraries"):
            raise HTTPException(status_code=404, detail="Nenhuma rota encontrada")
            
        # Extract the best itinerary (the first one)
        best_itinerary = data["plan"]["itineraries"][0]
        
        # Clean the response for our frontend
        clean_response = {
            "duration_minutes": math.ceil(best_itinerary["duration"] / 60),
            "walk_distance_meters": round(best_itinerary["walkDistance"]),
            "transfers": best_itinerary["transfers"],
            "legs": []
        }
        
        for leg in best_itinerary["legs"]:
            clean_leg = {
                "mode": leg["mode"], # WALK, BUS, TRAM, SUBWAY, RAIL
                "start_name": leg["from"]["name"],
                "end_name": leg["to"]["name"],
                "distance": round(leg["distance"]),
                "duration_minutes": math.ceil(leg["duration"] / 60),
                "polyline": leg["legGeometry"]["points"], # Encoded polyline for the map
            }
            
            # Transit specific info
            if leg["mode"] != "WALK":
                clean_leg["route"] = leg.get("route", "")
                clean_leg["agency"] = leg.get("agencyName", "")
                clean_leg["color"] = leg.get("routeColor", "")
                clean_leg["headsign"] = leg.get("headsign", "")
                
                # Intermediate stops
                clean_leg["stops"] = [
                    {"name": stop["name"], "lat": stop["lat"], "lon": stop["lon"]}
                    for stop in leg.get("intermediateStops", [])
                ]
                
            clean_response["legs"].append(clean_leg)
            
        return clean_response

    except Exception as e:
        print("Erro OTP:", str(e))
        raise HTTPException(status_code=500, detail=str(e))

# Servir os ficheiros estáticos (HTML/JS do teste)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    print("Iniciando BFF do OTP em http://localhost:5001")
    uvicorn.run(app, host="0.0.0.0", port=5001)
