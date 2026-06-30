#!/bin/bash

# Kill any existing OTP or Python processes just in case
pkill -f "otp-2.5.0-shaded.jar"
pkill -f "main.py"

echo "Starting OpenTripPlanner (OTP) Server in the background..."
if [ -f "./otp-test/jdk-21.0.2/bin/java" ]; then
    JAVA_CMD="./otp-test/jdk-21.0.2/bin/java"
else
    JAVA_CMD="java"
fi
$JAVA_CMD -Xmx6G -jar otp-test/otp-2.5.0-shaded.jar --load --serve otp-test/ > otp_server.log 2>&1 &

echo "Waiting for OTP to boot (usually ~15 seconds)..."
sleep 5

echo "Starting Python FastAPI Backend..."
# We run the main app
python3 main.py
