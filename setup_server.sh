#!/bin/bash
# Server deployment script for Valencia Transit Map

echo "======================================"
echo "Valencia Transit Map - Server Setup"
echo "======================================"

# 1. System Requirements (Requires root/sudo)
echo "[1/4] Installing dependencies..."
sudo apt-get update
sudo apt-get install -y openjdk-21-jre-headless python3 python3-pip python3-venv wget unzip curl

# 2. Python Environment
echo "[2/4] Setting up Python Environment..."
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. OpenTripPlanner Data
echo "[3/4] Downloading OpenTripPlanner Engine and Maps..."
mkdir -p otp-test
cd otp-test

# Check if JAR exists, if not download it
if [ ! -f "otp-2.5.0-shaded.jar" ]; then
    echo "Downloading OTP 2.5.0..."
    wget -O otp-2.5.0-shaded.jar "https://repo1.maven.org/maven2/org/opentripplanner/otp/2.5.0/otp-2.5.0-shaded.jar"
fi

# Check if OSM exists, if not download it
if [ ! -f "valencia-latest.osm.pbf" ]; then
    echo "Downloading Valencia OSM data..."
    wget -O valencia-latest.osm.pbf "http://download.geofabrik.de/europe/spain/comunidad-valenciana-latest.osm.pbf"
fi

# 4. Building the Graph
echo "[4/4] Building the Transit Graph (This will take a few minutes)..."
java -Xmx6G -jar otp-2.5.0-shaded.jar --build .

cd ..
echo "======================================"
echo "Setup Complete!"
echo "You can now run: ./start_all.sh"
echo "======================================"
