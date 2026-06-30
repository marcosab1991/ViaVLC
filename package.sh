#!/bin/bash
echo "Packaging for production..."
mkdir -p dist/valencia-transit-server
cp main.py dist/valencia-transit-server/
cp requirements.txt dist/valencia-transit-server/
cp stops.db dist/valencia-transit-server/
cp lines.db dist/valencia-transit-server/
cp metro_wp_mapping.json dist/valencia-transit-server/
cp tram_wp_mapping.json dist/valencia-transit-server/
cp start_all.sh dist/valencia-transit-server/
cp -r static dist/valencia-transit-server/

mkdir -p dist/valencia-transit-server/otp-test
cp otp-test/otp-2.5.0-shaded.jar dist/valencia-transit-server/otp-test/
cp otp-test/graph.obj dist/valencia-transit-server/otp-test/
cp -r otp-test/jdk-21.0.2 dist/valencia-transit-server/otp-test/

cd dist
zip -r valencia-transit-server.zip valencia-transit-server
echo "Packaging complete!"
