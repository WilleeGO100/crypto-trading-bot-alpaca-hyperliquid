@echo off
title Quant Engine - Server

echo ===================================================
echo 🚀 INITIATING QUANT ENGINE STARTUP SEQUENCE...
echo ===================================================

:: Navigate to the project folder
cd /d "C:\Users\jwmar\Documents\Desktop\hyperliquid-python-sdk-0.22.0"

:: Force it to use the Uvicorn inside the virtual environment
".venv\Scripts\uvicorn.exe" signal_receiver:app --reload

pause