@echo off
title Quant Engine - Discord Bot

echo ===================================================
echo 🤖 INITIATING DISCORD LISTENER...
echo ===================================================

:: Navigate to the project folder
cd /d "C:\Users\jwmar\Documents\Desktop\hyperliquid-python-sdk-0.22.0"

:: Force it to use the Python inside the virtual environment
".venv\Scripts\python.exe" discord_listener.py

pause