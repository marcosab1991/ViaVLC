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
const userIcon = createIcon('#10b981'); // Green for user location

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

// Handle Search
const searchInput = document.getElementById('search-input');
const searchResults = document.getElementById('search-results');
let searchTimeout = null;

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
                searchResults.innerHTML = data.data.map(stop => `
                    <div class="search-result-item" data-lat="${stop.location.lat}" data-lng="${stop.location.lng}" data-id="${stop.id}">
                        <span class="line-badge ${stop.type === 'bus' ? 'bus-line' : 'metro-line'}">${stop.type === 'bus' ? 'EMT' : 'Metro'}</span>
                        ${stop.name}
                    </div>
                `).join('');
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
    // We do NOT clear all markers, instead we add new ones. 
    // For a real production app we'd remove old ones far from view, but this is fine for now.
    
    let addedCount = 0;
    
    stops.forEach(stop => {
        // Prevent duplicate markers
        if (activeMarkers[stop.id]) return;
        
        const isBus = stop.type === 'bus';
        const icon = isBus ? busIcon : metroIcon;
        const typeLabel = isBus ? 'EMT Autobús' : 'Metrovalencia';
        
        const marker = L.marker([stop.location.lat, stop.location.lng], { icon });
        activeMarkers[stop.id] = marker;
        markersLayer.addLayer(marker);
        
        // Prepare initial popup with loading state
        marker.bindPopup('', {
            className: 'custom-popup',
            closeButton: false,
            minWidth: 200
        });

        // Event listener for click to fetch real-time ETA
        marker.on('click', async (e) => {
            await loadStopData(marker, stop);
        });
    });
}

async function loadStopData(marker, stop, filterLine = null) {
    const popup = marker.getPopup();
    const isBus = stop.type === 'bus';
    const typeLabel = isBus ? 'EMT Autobús' : 'Metrovalencia';
    
    // Set loading content
    popup.setContent(`
        <div class="popup-title">${stop.name}</div>
        <div class="popup-type">${typeLabel}</div>
        <div class="loading-pulse">Cargando tiempos...</div>
    `);
            
async function fetchDirectBusEta(stopId) {
    try {
        // Fetch directly from Geoportal to bypass Render blocking AWS IPs!
        const response = await fetch(`https://geoportal.emtvalencia.es/EMT/mapfunctions/MapUtilsPetitions.php?sec=getSAE&parada=${stopId}`);
        if (!response.ok) return { success: false };
        const text = await response.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(text, 'text/xml');
        const buses = doc.getElementsByTagName('bus');
        const arrivals = [];
        for (let i = 0; i < buses.length; i++) {
            const bus = buses[i];
            const lineaNode = bus.getElementsByTagName('linea')[0];
            const destinoNode = bus.getElementsByTagName('destino')[0];
            const minutosNode = bus.getElementsByTagName('minutos')[0];
            const horaLlegada = bus.getElementsByTagName('horaLlegada')[0];
            const errorNode = bus.getElementsByTagName('error')[0];
            
            if (errorNode && errorNode.textContent && errorNode.textContent !== 'null') continue;
            
            let timeText = '? min';
            if (minutosNode && minutosNode.textContent) {
                let m = minutosNode.textContent;
                if (m.includes('min.')) timeText = m.replace(' min.', '') + ' min';
                else if (m.startsWith('Pr')) timeText = '1 min';
                else timeText = m + ' min';
            } else if (horaLlegada && horaLlegada.textContent) {
                timeText = horaLlegada.textContent;
            }
            
            if (lineaNode && destinoNode) {
                arrivals.push({ line: lineaNode.textContent, destination: destinoNode.textContent, eta: timeText });
            }
        }
        return { success: true, data: arrivals, cached: false };
    } catch(e) {
        return { success: false };
    }
}

async function fetchDirectMetroEta(stopId) {
    try {
        const response = await fetch('https://www.metrovalencia.es/wp-admin/admin-ajax.php', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: `action=formularios_ajax&data=action%3Dinfo-estacion%26id%3D${stopId}`
        });
        const data = await response.json();
        if (!data || !data.html) return { success: true, data: [] };
        
        const parser = new DOMParser();
        const doc = parser.parseFromString(data.html, 'text/html');
        const items = doc.querySelectorAll('.item--proximos');
        const arrivals = [];
        items.forEach(item => {
            const lineEl = item.querySelector('.linea');
            const destEl = item.querySelector('.nombre-estacion');
            const timeEl = item.querySelector('.minutos');
            if (lineEl && destEl && timeEl) {
                const lineClasses = Array.from(lineEl.classList);
                let lineStr = 'L?';
                for (let c of lineClasses) {
                    if (c.startsWith('linea-') && c !== 'linea-') {
                        lineStr = 'L' + c.split('-')[1];
                    }
                }
                arrivals.push({
                    line: lineStr,
                    destination: destEl.textContent.trim(),
                    eta: timeEl.textContent.trim()
                });
            }
        });
        return { success: true, data: arrivals, cached: false };
    } catch(e) {
        console.error("CORS Error Metro:", e);
        return { success: false, error: "Erro de CORS ou Conexão" };
    }
}

            try {
                let data;
                if (stop.type === 'bus') {
                    data = await fetchDirectBusEta(stop.id);
                } else {
                    // Reverted back to backend to avoid CORS errors
                    const response = await fetch(`/api/eta?id=${stop.id}&type=${stop.type}`);
                    data = await response.json();
                }
                
                if (data.success) {
                    let linesHtml = '';
                    
                    // Filter arrivals if filterLine is provided
                    let arrivals = data.data;
                    if (filterLine) {
                        arrivals = arrivals.filter(a => a.line === filterLine);
                    }
                    
                    if (arrivals.length === 0) {
                        linesHtml = '<div class="no-data">No hay próximas llegadas de esta línea</div>';
                    } else {
                        const lineClass = isBus ? 'bus-line' : 'metro-line';
                        linesHtml = arrivals.map(arrival => `
                            <div class="arrival-item" onclick="drawRoute('${arrival.line}', '${stop.type}', '${stop.id}')" style="cursor: pointer;" title="Ver Ruta">
                                <div class="arrival-left">
                                    <span class="line-badge ${lineClass}">${arrival.line}</span>
                                    ${arrival.destination ? `<span class="arrival-dest">${arrival.destination}</span>` : ''}
                                </div>
                                <span class="eta-time">${arrival.eta}</span>
                            </div>
                        `).join('');
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
                popup.setContent(`
                    <div class="popup-title">${stop.name}</div>
                    <div class="error-msg">Error de conexión</div>
                `);
            }
}


// Draw route function
async function drawRoute(line, type, originStopId = null) {
    // Clear previous
    if (currentRouteLayer) map.removeLayer(currentRouteLayer);
    if (routeStopsLayer) map.removeLayer(routeStopsLayer);
    
    // Hide all other markers
    map.removeLayer(markersLayer);
    
    // Show exit button
    document.getElementById('exit-route-btn').style.display = 'block';
    
    // Dim the base map and shrink header on mobile
    document.getElementById('map').classList.add('route-mode-active');
    document.querySelector('.main-header').classList.add('route-mode-active');
    
    // Close popup
    map.closePopup();
    const statusText = document.getElementById('status-text');
    statusText.innerText = `Cargando ruta de L${line}...`;
    statusText.style.color = '#3b82f6';
    
    try {
        const response = await fetch(`/api/line_geometry?line=${line}`);
        const data = await response.json();
        
        if (!data.success) throw new Error(data.error);
        
        // Draw route line - DISABLED due to bidirectional zig-zag issues without GTFS
        /*
        currentRouteLayer = L.geoJSON(data.geometry, {
            style: { color: '#ef4444', weight: 4, opacity: 0.8 }
        }).addTo(map);
        */
        
        // Draw stops
        routeStopsLayer = L.layerGroup().addTo(map);
        let originMarker = null;
        
        const routeColor = type === 'metro' ? '#3b82f6' : '#ef4444'; // Blue for Metro, Red for Bus
        
        data.ordered_stops.forEach((stop, i) => {
            const isOrigin = originStopId && (stop.id == originStopId || stop.id == `metro-${originStopId}` || `metro-${stop.id}` == originStopId);
            const marker = L.circleMarker([stop.lat, stop.lng], {
                radius: isOrigin ? 10 : 6,
                fillColor: isOrigin ? '#10b981' : '#ffffff',
                color: isOrigin ? '#059669' : routeColor,
                weight: isOrigin ? 3 : 2,
                fillOpacity: 1
            }).bindTooltip(`${i+1}. ${stop.name}`);
            
            // Allow clicking the stop to show its specific ETA
            marker.bindPopup('', {
                className: 'custom-popup',
                closeButton: false,
                minWidth: 200
            });
            
            marker.on('click', async (e) => {
                map.setView([stop.lat, stop.lng], 18);
                // The 'stop' object here is missing the 'type' field because the API doesn't return it
                // We add it manually based on the drawRoute parameter
                stop.type = type || 'bus'; 
                await loadStopData(marker, stop, line);
            });
            
            routeStopsLayer.addLayer(marker);
            if (isOrigin) originMarker = marker;
        });
        
        // Use stops layer bounds since route line is disabled
        const bounds = L.latLngBounds(data.ordered_stops.map(s => [s.lat, s.lng]));
        map.fitBounds(bounds, { padding: [50, 50] });
        
        if (originMarker) {
            // Open the popup for the selected station after map centers
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
        
        // If error, restore markers
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

