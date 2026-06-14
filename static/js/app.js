// Valencia Coordinates (Fallback)
const VALENCIA_COORDS = [39.4699, -0.3763];
const DEFAULT_ZOOM = 14;

// Initialize Map
const map = L.map('map', {
    zoomControl: false 
}).setView(VALENCIA_COORDS, DEFAULT_ZOOM);

// Add OpenStreetMap tiles
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

// Add custom zoom control position
L.control.zoom({
    position: 'bottomright'
}).addTo(map);

// Custom Icons
const createIcon = (color) => {
    return L.divIcon({
        className: 'custom-icon',
        html: `<div style="
            width: 16px; 
            height: 16px; 
            background-color: ${color}; 
            border: 2px solid white; 
            border-radius: 50%;
            box-shadow: 0 0 10px ${color};
        "></div>`,
        iconSize: [16, 16],
        iconAnchor: [8, 8],
        popupAnchor: [0, -10]
    });
};

const busIcon = createIcon('#ef4444');
const metroIcon = createIcon('#3b82f6');
const tramIcon = createIcon('#f97316'); // Orange for TRAM
const metrobusIcon = createIcon('#FFB81C'); // Yellow for Metrobús

// Map click closes popup and search
map.on('click', () => {
    searchResults.classList.add('hidden');
});

const userIcon = createIcon('#10b981'); // Green for user location

function createHybridIcon(color1, color2) {
    return L.divIcon({
        className: 'custom-icon',
        html: `<div style="
            width: 100%;
            height: 100%;
            border-radius: 50%;
            background: linear-gradient(135deg, ${color1} 50%, ${color2} 50%);
            border: 2px solid white;
            box-shadow: 0 0 5px rgba(0,0,0,0.5);
        "></div>`,
        iconSize: [16, 16],
        iconAnchor: [8, 8],
        popupAnchor: [0, -10]
    });
}

function clusterStops(stops) {
    const clusters = [];
    const thresholdSq = 0.00000004; // Very small radius (~20 meters)
    const used = new Set();
    
    for (let i = 0; i < stops.length; i++) {
        if (used.has(stops[i].id)) continue;
        
        const cluster = {
            id: `cluster-${stops[i].id}`,
            members: [stops[i]],
            location: { lat: stops[i].location.lat, lng: stops[i].location.lng }
        };
        used.add(stops[i].id);
        
        for (let j = i + 1; j < stops.length; j++) {
            if (used.has(stops[j].id)) continue;
            
            // Prevent clustering road networks (bus/metrobus) with rail networks (metro/tram)
            const isIRail = stops[i].type === 'metro' || stops[i].type === 'tram';
            const isJRail = stops[j].type === 'metro' || stops[j].type === 'tram';
            if (isIRail !== isJRail) continue;
            
            const distSq = Math.pow(stops[i].location.lat - stops[j].location.lat, 2) + 
                           Math.pow(stops[i].location.lng - stops[j].location.lng, 2);
            
            if (distSq < thresholdSq) {
                // If it's the exact same transport type, maybe keep them separate unless they have same name?
                // Actually, let's group anything extremely close to simplify the map and aggregate data
                cluster.members.push(stops[j]);
                used.add(stops[j].id);
            }
        }
        clusters.push(cluster);
    }
    return clusters;
}

let markersLayer = L.layerGroup().addTo(map);
let activeMarkers = {}; // Keep track of rendered markers by ID
let currentRouteLayer = null; // Store current OSRM route layer
let routeStopsLayer = null; // Store route stops by ID
let userMarker = null;
let userCurrentLatLng = null;

// Handle Header Toggle
const headerTop = document.getElementById('header-top');
const headerBody = document.getElementById('header-body');
headerTop.addEventListener('click', (e) => {
    // Don't toggle if clicking the search input
    if (e.target.id === 'search-input') return;
    headerBody.classList.toggle('collapsed');
    headerTop.classList.toggle('collapsed');
});

// Handle Search and Zones
const searchInput = document.getElementById('search-input');
const searchResults = document.getElementById('search-results');
const zoneSelector = document.getElementById('zone-selector');
let searchTimeout = null;

let showNetwork = {
    bus: true,
    metro: true,
    tram: true,
    metrobus: true
};

// Transport Filters (using Legend)
document.querySelectorAll('.legend-item').forEach(btn => {
    btn.addEventListener('click', () => {
        const network = btn.getAttribute('data-network');
        showNetwork[network] = !showNetwork[network];
        btn.classList.toggle('inactive', !showNetwork[network]);
        
        // Clear all current markers from the layer group and re-fetch
        markersLayer.clearLayers();
        activeMarkers = {};
        fetchStopsInView();
    });
});

const ZONES = {
    'valencia': [39.4699, -0.3763],
    'alicante': [38.3452, -0.4810]
};

if (zoneSelector) {
    zoneSelector.addEventListener('change', (e) => {
        const coords = ZONES[e.target.value];
        if (coords) {
            map.flyTo(coords, 14, { duration: 1.5 });
            setTimeout(fetchStopsInView, 1500);
        }
    });
}

const metroColors = {
    'L1': '#e4be36',
    'L2': '#b4397f',
    'L3': '#b11d2f',
    'L4': '#2b498b',
    'L5': '#4e886d',
    'L6': '#817fb3',
    'L7': '#ce7d28',
    'L8': '#96c4da',
    'L9': '#a47e52',
    'L10': '#a47e52'
};

const tramColors = {
    'L1': '#e12c29',
    'L2': '#52a144',
    'L3': '#fdc300',
    'L4': '#8a348e',
    'L5': '#0054a4',
    'L9': '#7f8084'
};

// Add Exit Route Button to Map
const exitRouteBtn = L.control({position: 'topright'});
exitRouteBtn.onAdd = function(map) {
    const div = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
    div.innerHTML = `
        <button id="exit-route-btn" class="exit-route-btn" style="display:none;">
            <span class="icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg></span>
            <span class="text">Salir de Ruta</span>
        </button>
    `;
    return div;
};
exitRouteBtn.addTo(map);

document.getElementById('exit-route-btn').addEventListener('click', () => {
    if (currentRouteLayer) map.removeLayer(currentRouteLayer);
    if (routeStopsLayer) map.removeLayer(routeStopsLayer);
    currentRouteLayer = null;
    routeStopsLayer = null;
    map.addLayer(markersLayer); // Restore all markers
    document.getElementById('map').classList.remove('route-mode-active');
    document.querySelector('.main-header').classList.remove('route-mode-active');
    document.getElementById('exit-route-btn').style.display = 'none';
    document.getElementById('status-text').innerText = 'Ruta cerrada.';
    document.getElementById('status-text').style.color = '#6b7280';
});

searchInput.addEventListener('input', (e) => {
    const query = e.target.value.trim();
    if (query.length < 2) {
        searchResults.classList.add('hidden');
        return;
    }
    
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(async () => {
        try {
            const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
            const data = await res.json();
            if (data.success && data.data.length > 0) {
                searchResults.innerHTML = data.data.map(stop => {
                    const isBus = stop.type === 'bus';
                    const isTram = stop.type === 'tram';
                    const isMetrobus = stop.type === 'metrobus';
                    const badgeClass = isBus ? 'bus-line' : (isTram ? 'tram-line' : (isMetrobus ? 'metrobus-line' : 'metro-line'));
                    const badgeText = isBus ? 'EMT' : (isTram ? 'TRAM' : (isMetrobus ? 'M-Bus' : 'Metro'));
                    const badgeStyle = isTram ? 'background-color: #f97316; color: white; border: none;' : (isMetrobus ? 'background-color: #FFB81C; color: black; border: none;' : '');
                    
                    return `
                    <div class="search-result-item" data-lat="${stop.location.lat}" data-lng="${stop.location.lng}" data-id="${stop.id}">
                        <span class="line-badge ${badgeClass}" style="${badgeStyle}">${badgeText}</span>
                        ${stop.name}
                    </div>
                    `;
                }).join('');
                searchResults.classList.remove('hidden');
            } else {
                searchResults.innerHTML = '<div class="search-result-item" style="color:#fca5a5;">No se encontraron resultados</div>';
                searchResults.classList.remove('hidden');
            }
        } catch (err) {
            console.error(err);
        }
    }, 300);
});

// Handle click on search result
searchResults.addEventListener('click', (e) => {
    const item = e.target.closest('.search-result-item');
    if (item && item.dataset.lat) {
        const lat = parseFloat(item.dataset.lat);
        const lng = parseFloat(item.dataset.lng);
        map.setView([lat, lng], 18);
        searchResults.classList.add('hidden');
        searchInput.value = '';
        headerBody.classList.add('collapsed'); // Collapse header to see map
        headerTop.classList.add('collapsed');
    }
});

// Close search if clicked outside
document.addEventListener('click', (e) => {
    if (!e.target.closest('.search-container')) {
        searchResults.classList.add('hidden');
    }
});

// Handle Geolocation with HTML5 native API for continuous tracking
if ("geolocation" in navigator) {
    let isFirstLocation = true;
    
    navigator.geolocation.watchPosition(
        function(position) {
            userCurrentLatLng = [position.coords.latitude, position.coords.longitude];
            
            if (isFirstLocation) {
                // Force center and closer zoom on initial load
                map.setView(userCurrentLatLng, 18);
                isFirstLocation = false;
            }
            
            if (!userMarker) {
                userMarker = L.marker(userCurrentLatLng, { icon: userIcon }).addTo(map)
                    .bindPopup('<div class="popup-title">Tu ubicación</div>', { className: 'custom-popup' });
            } else {
                userMarker.setLatLng(userCurrentLatLng);
            }
        },
        function(error) {
            console.warn("Geolocation error:", error.message);
            if (isFirstLocation) fetchStopsInView(); // Fallback
        },
        {
            enableHighAccuracy: true,
            timeout: 10000,
            maximumAge: 0
        }
    );
} else {
    console.warn("Geolocation not supported by this browser.");
    fetchStopsInView();
}

// Locate Me Button
const locateBtn = document.getElementById('locate-btn');
locateBtn.addEventListener('click', () => {
    if (userCurrentLatLng) {
        map.setView(userCurrentLatLng, 18);
    } else {
        alert("Buscando tu ubicación... por favor espera o permite el acceso al GPS.");
    }
});

// Fetch stops when user stops panning/zooming
map.on('moveend', fetchStopsInView);

async function fetchStopsInView() {
    const statusText = document.getElementById('status-text');
    statusText.innerText = 'Actualizando paradas...';
    
    const bounds = map.getBounds();
    const min_lat = bounds.getSouth();
    const max_lat = bounds.getNorth();
    const min_lng = bounds.getWest();
    const max_lng = bounds.getEast();
    
    try {
        const url = `/api/stops?min_lat=${min_lat}&max_lat=${max_lat}&min_lng=${min_lng}&max_lng=${max_lng}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        
        const data = await response.json();
        if (data.success) {
            renderMarkers(data.data);
            statusText.innerText = `Mostrando ${data.data.length} paradas locales`;
        } else {
            throw new Error(data.error);
        }
    } catch (error) {
        console.error("Error fetching stops:", error);
        statusText.innerText = 'Error al cargar paradas';
        statusText.style.color = '#ef4444';
    }
}

// Render markers on the map
function renderMarkers(stops) {
    const clusters = clusterStops(stops);
    
    clusters.forEach(cluster => {
        // Filter members by active networks
        const activeMembers = cluster.members.filter(m => showNetwork[m.type]);
        if (activeMembers.length === 0) return;
        
        const activeIds = activeMembers.map(m => m.id).sort().join('-');
        if (activeMarkers[activeIds]) return; // This exact cluster is already rendered
        
        // Clean up any stale subset/superset markers that share members with this new cluster
        activeMembers.forEach(m => {
            Object.keys(activeMarkers).forEach(key => {
                if (key.split('-').includes(m.id)) {
                    markersLayer.removeLayer(activeMarkers[key]);
                    delete activeMarkers[key];
                }
            });
        });
        
        let icon;
        const types = [...new Set(activeMembers.map(m => m.type))];
        if (types.length === 1) {
            const type = types[0];
            icon = type === 'bus' ? busIcon : (type === 'tram' ? tramIcon : (type === 'metrobus' ? metrobusIcon : metroIcon));
        } else {
            if (types.includes('bus') && types.includes('metrobus') && types.length === 2) {
                icon = createHybridIcon('#ef4444', '#FFB81C');
            } else if (types.includes('bus') && types.includes('metro')) {
                icon = createHybridIcon('#ef4444', '#3b82f6');
            } else {
                icon = createHybridIcon('#ef4444', '#FFB81C'); // fallback
            }
        }
        
        const marker = L.marker([cluster.location.lat, cluster.location.lng], { icon });
        const popup = L.popup({
            className: 'custom-popup',
            closeButton: false,
            minWidth: 200
        }).setContent('<div class="loading-pulse">Cargando tiempos...</div>');
        
        marker.bindPopup(popup);
        activeMarkers[activeIds] = marker;
        markersLayer.addLayer(marker);
        
        // Event listener for click to fetch real-time ETA
        marker.on('click', async (e) => {
            // Content will be updated by loadStopData / loadClusterData
            if (activeMembers.length === 1) {
                await loadStopData(marker, activeMembers[0]);
            } else {
                await loadClusterData(marker, activeMembers);
            }
        });
    });
}

async function fetchDirectBusEta(stopId) {
    try {
        const response = await fetch(`https://geoportal.emtvalencia.es/EMT/mapfunctions/MapUtilsPetitions.php?sec=getSAE&parada=${stopId}`);
        if (!response.ok) return { success: false };
        const text = await response.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(text, 'text/xml');
        const buses = doc.getElementsByTagName('bus');
        const arrivals = [];
        for (let i = 0; i < buses.length; i++) {
            const bus = buses[i];
            const linea = bus.getElementsByTagName('linea')[0]?.textContent;
            const destino = bus.getElementsByTagName('destino')[0]?.textContent;
            const minutos = bus.getElementsByTagName('minutos')[0]?.textContent;
            if (linea && destino && minutos) {
                arrivals.push({
                    line: linea,
                    destination: destino.replace('<![CDATA[', '').replace(']]>', ''),
                    eta: minutos
                });
            }
        }
        return { success: true, data: arrivals, cached: false };
    } catch (e) {
        return { success: false };
    }
}

async function loadStopData(marker, stop, filterLine = null) {
    const popup = marker.getPopup();
    if (!popup) return;
    const isBus = stop.type === 'bus';
    const isTram = stop.type === 'tram';
    const isMetrobus = stop.type === 'metrobus';
    const typeLabel = isBus ? "EMT Autobús" : (isTram ? "TRAM d'Alacant" : (isMetrobus ? "Metrobús" : "Metrovalencia"));
    
    // Set loading content
    popup.setContent(`
        <div class="popup-title">${stop.name}</div>
        <div class="popup-type">${typeLabel}</div>
        <div class="loading-pulse">Cargando tiempos...</div>
    `);
    
    try {
        let data;
        if (stop.type === 'bus') {
            data = await fetchDirectBusEta(stop.id);
        } else {
            const response = await fetch(`/api/eta?id=${stop.id}&type=${stop.type}`);
            data = await response.json();
        }
        if (data.success) {
            let linesHtml = '';
            let arrivals = data.data;
            
            if (arrivals.length === 0) {
                linesHtml = stop.type === 'metrobus' ? '<div class="no-data">Temporización no disponible para Metrobús.</div>' : '<div class="no-data">No hay próximas llegadas en esta parada</div>';
            } else {
                const lineClass = isBus ? 'bus-line' : (isTram ? 'tram-line' : (isMetrobus ? 'metrobus-line' : 'metro-line'));
                linesHtml = arrivals.map(arrival => {
                    const colorsMap = isTram ? tramColors : metroColors;
                    let badgeStyle = '';
                    if (isTram && colorsMap[arrival.line]) badgeStyle = `background-color: ${colorsMap[arrival.line]}; color: white; border: none;`;
                    else if (!isBus && !isMetrobus && colorsMap[arrival.line]) badgeStyle = `background-color: ${colorsMap[arrival.line]}; color: white; border: none;`;
                    else if (isMetrobus) badgeStyle = 'background-color: #FFB81C; color: black; border: none;';
                    
                    let displayEta = arrival.eta;
                    if (displayEta.includes(':')) {
                        const parts = displayEta.split(':');
                        if (parts.length >= 2) {
                            const totalMins = parseInt(parts[0]) * 60 + parseInt(parts[1]);
                            displayEta = `${totalMins} min.`;
                        }
                    }
                    
                    return `
                    <div class="arrival-item" onclick="drawRoute('${arrival.line}', '${stop.type}', '${stop.id}', '${(arrival.destination || '').replace(/'/g, "\\'")}')" style="cursor: pointer;" title="Ver Ruta">
                        <div class="arrival-left">
                            <span class="line-badge ${lineClass}" style="${badgeStyle}">${arrival.line}</span>
                            ${arrival.destination ? `<span class="arrival-dest">${arrival.destination}</span>` : ''}
                        </div>
                        <span class="eta-time">${displayEta}</span>
                    </div>
                    `;
                }).join('');
            }
            
            popup.setContent(`
                <div class="popup-title">${stop.name}</div>
                <div class="popup-type">${typeLabel}${data.cached ? ' <span style="color:#10b981; font-size:0.6rem;">(Cached)</span>' : ''}</div>
                <div class="arrivals-container">
                    ${linesHtml}
                </div>
            `);
        } else {
            popup.setContent(`
                <div class="popup-title">${stop.name}</div>
                <div class="error-msg">Error al cargar datos</div>
            `);
        }
    } catch (error) {
        console.error("Error fetching ETA:", error);
        popup.setContent(`
            <div class="popup-title">${stop.name}</div>
            <div class="error-msg">Error al cargar datos</div>
        `);
    }
}

async function loadClusterData(marker, activeMembers) {
    const popup = marker.getPopup();
    
    // Combine names uniquely
    const names = [...new Set(activeMembers.map(m => m.name))].join(' / ');
    const types = [...new Set(activeMembers.map(m => m.type === 'bus' ? 'EMT' : (m.type === 'metrobus' ? 'Metrobús' : (m.type === 'tram' ? 'TRAM' : 'Metrovalencia'))))].join(' + ');
    
    popup.setContent(`
        <div class="popup-title">${names}</div>
        <div class="popup-type">${types}</div>
        <div class="loading-pulse">Cargando tiempos...</div>
    `);
    
    let allArrivals = [];
    let hasError = false;
    let isCached = false;
    
    const promises = activeMembers.map(async (stop) => {
        try {
            let data;
            if (stop.type === 'bus') {
                data = await fetchDirectBusEta(stop.id);
                if (data.success && data.data) {
                    data.data.forEach(arr => arr._parentType = 'bus');
                    allArrivals.push(...data.data);
                } else {
                    hasError = true;
                }
            } else {
                const response = await fetch(`/api/eta?id=${stop.id}&type=${stop.type}`);
                data = await response.json();
                if (data.success && data.data) {
                    data.data.forEach(arr => arr._parentType = stop.type);
                    allArrivals.push(...data.data);
                    if (data.cached) isCached = true;
                } else {
                    hasError = true;
                }
            }
        } catch (e) {
            hasError = true;
        }
    });
    
    await Promise.all(promises);
    
    if (allArrivals.length === 0) {
        popup.setContent(`
            <div class="popup-title">${names}</div>
            <div class="error-msg">No hay próximas llegadas en esta parada</div>
        `);
        return;
    }
    
    // Sort all arrivals by extracted minutes
    allArrivals.sort((a, b) => {
        const extractMin = (str) => {
            if (!str) return 999;
            if (str.includes(':')) {
                const parts = str.split(':');
                if (parts.length >= 2) {
                    return parseInt(parts[0]) * 60 + parseInt(parts[1]);
                }
            }
            const m = str.match(/\d+/);
            return m ? parseInt(m[0]) : 999;
        };
        return extractMin(a.eta) - extractMin(b.eta);
    });
    
    let linesHtml = allArrivals.map(arrival => {
        const type = arrival._parentType;
        const isBus = type === 'bus';
        const isTram = type === 'tram';
        const isMetrobus = type === 'metrobus';
        const lineClass = isBus ? 'bus-line' : (isTram ? 'tram-line' : (isMetrobus ? 'metrobus-line' : 'metro-line'));
        
        const colorsMap = isTram ? tramColors : metroColors;
        let badgeStyle = '';
        if (arrival.color) badgeStyle = `background-color: #${arrival.color}; color: white; border: none;`;
        else if (isTram && colorsMap[arrival.line]) badgeStyle = `background-color: ${colorsMap[arrival.line]}; color: white; border: none;`;
        else if (!isBus && !isMetrobus && colorsMap[arrival.line]) badgeStyle = `background-color: ${colorsMap[arrival.line]}; color: white; border: none;`;
        else if (isMetrobus) badgeStyle = 'background-color: #FFB81C; color: black; border: none;';
        
        const originStop = activeMembers.find(m => m.type === type);
        
        let displayEta = arrival.eta;
        if (displayEta.includes(':')) {
            const parts = displayEta.split(':');
            if (parts.length >= 2) {
                const totalMins = parseInt(parts[0]) * 60 + parseInt(parts[1]);
                displayEta = `${totalMins} min.`;
            }
        }
        
        return `
        <div class="arrival-item" onclick="drawRoute('${arrival.line}', '${type}', '${originStop.id}', '${(arrival.destination || '').replace(/'/g, "\\'")}')" style="cursor: pointer;" title="Ver Ruta">
            <div class="arrival-left">
                <span class="line-badge ${lineClass}" style="${badgeStyle}">${arrival.line}</span>
                ${arrival.destination ? `<span class="arrival-dest">${arrival.destination}</span>` : ''}
            </div>
            <span class="eta-time">${displayEta}</span>
        </div>
        `;
    }).join('');
    
    popup.setContent(`
        <div class="popup-title">${names}</div>
        <div class="popup-type">${types}${isCached ? ' <span style="color:#10b981; font-size:0.6rem;">(Cached)</span>' : ''}</div>
        <div class="arrivals-container">
            ${linesHtml}
        </div>
    `);
}

// Draw route function
async function drawRoute(line, type, originStopId = null, destination = null) {
    if (currentRouteLayer) map.removeLayer(currentRouteLayer);
    if (routeStopsLayer) map.removeLayer(routeStopsLayer);
    map.removeLayer(markersLayer);
    
    document.getElementById('exit-route-btn').style.display = 'block';
    document.getElementById('map').classList.add('route-mode-active');
    document.querySelector('.main-header').classList.add('route-mode-active');
    map.closePopup();
    
    const statusText = document.getElementById('status-text');
    statusText.innerText = `Cargando ruta L${line}...`;
    statusText.style.color = '#3b82f6';
    
    try {
        const url = `/api/line_geometry?line=${line}&type=${type}${destination ? '&destination=' + encodeURIComponent(destination) : ''}`;
        const response = await fetch(url);
        const data = await response.json();
        
        if (!data.success) throw new Error(data.error || 'Failed to fetch route');
        
        const isTram = type === 'tram';
        const isMetrobus = type === 'metrobus';
        const colorsMap = isTram ? tramColors : metroColors;
        let routeColor = '#ef4444';
        if (isTram) routeColor = colorsMap[line] || '#f97316';
        else if (type === 'metro') routeColor = colorsMap[line] || '#3b82f6';
        else if (isMetrobus) routeColor = '#FFB81C';
        
        if (data.geometry) {
            currentRouteLayer = L.geoJSON(data.geometry, {
                style: { color: routeColor, weight: 4, opacity: 0.8 }
            }).addTo(map);
        } else {
            const latlngs = data.ordered_stops.map(s => [s.lat, s.lng]);
            currentRouteLayer = L.polyline(latlngs, {
                color: routeColor,
                weight: 4,
                opacity: 0.8,
                dashArray: '10, 10'
            }).addTo(map);
        }
        
        routeStopsLayer = L.layerGroup().addTo(map);
        let originMarker = null;
        
        data.ordered_stops.forEach((stop, i) => {
            const isOrigin = originStopId && (stop.id == originStopId || stop.id == `metro-${originStopId}` || `metro-${stop.id}` == originStopId);
            const marker = L.circleMarker([stop.lat, stop.lng], {
                radius: isOrigin ? 10 : 6,
                fillColor: isOrigin ? '#10b981' : '#ffffff',
                color: isOrigin ? '#059669' : routeColor,
                weight: isOrigin ? 3 : 2,
                fillOpacity: 1
            }).bindTooltip(`${type === 'bus' ? i+1 + '. ' : ''}${stop.name}`);
            
            marker.bindPopup('', {
                className: 'custom-popup',
                closeButton: false,
                minWidth: 200
            });
            
            marker.on('click', async (e) => {
                stop.type = type || 'bus'; 
                await loadStopData(marker, stop, line);
            });
            
            routeStopsLayer.addLayer(marker);
            if (isOrigin) originMarker = marker;
        });
        
        if (data.ordered_stops.length > 0) {
            const bounds = L.latLngBounds(data.ordered_stops.map(s => [s.lat, s.lng]));
            map.fitBounds(bounds, { padding: [50, 50] });
        }
        
        if (originMarker) {
            setTimeout(() => {
                originMarker.openPopup();
                originMarker.fire('click');
            }, 500);
        }
        
        statusText.innerText = `Mostrando L${line} (${data.ordered_stops.length} paradas)`;
        statusText.style.color = '#10b981';
        
    } catch (e) {
        console.error("Route error:", e);
        statusText.innerText = `Error cargando ruta L${line}`;
        statusText.style.color = '#ef4444';
        map.addLayer(markersLayer);
        document.getElementById('map').classList.remove('route-mode-active');
        document.querySelector('.main-header').classList.remove('route-mode-active');
        document.getElementById('exit-route-btn').style.display = 'none';
    }
}

// PWA Registration & Install Logic
let deferredPrompt;
const pwaInstallBtn = document.getElementById('pwa-install-btn');

const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone;

if (!isStandalone && isIOS) {
    // iOS does not fire beforeinstallprompt, show button manually if not installed
    pwaInstallBtn.style.display = 'flex';
}

window.addEventListener('beforeinstallprompt', (e) => {
    // Prevent the mini-infobar from appearing on mobile Chrome
    e.preventDefault();
    deferredPrompt = e;
    pwaInstallBtn.style.display = 'flex';
});

// DEBUG/FALLBACK: Show button after 1.5s anyway to test if PWA is active
setTimeout(() => {
    if (!isStandalone) {
        pwaInstallBtn.style.display = 'flex';
    }
}, 1500);

pwaInstallBtn.addEventListener('click', async () => {
    if (isIOS) {
        alert("Para instalar en iPhone/iPad:\n1. Toca el botón de 'Compartir' (cuadrado con flecha hacia arriba)\n2. Toca 'Añadir a la pantalla de inicio'");
        return;
    }

    if (deferredPrompt) {
        deferredPrompt.prompt();
        const { outcome } = await deferredPrompt.userChoice;
        if (outcome === 'accepted') {
            pwaInstallBtn.style.display = 'none';
        }
        deferredPrompt = null;
    } else {
        alert("El navegador aún no permite la instalación automática. Revisa que estés accediendo con 'https://' o instala manualmente desde el menú del navegador (⋮) -> 'Añadir a la pantalla de inicio'.");
    }
});

window.addEventListener('appinstalled', () => {
    pwaInstallBtn.style.display = 'none';
    deferredPrompt = null;
});

// Register Service Worker
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').catch(err => {
            console.error('SW registration failed:', err);
        });
    });
}

