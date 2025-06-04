#!/bin/bash

echo "Setting up ESP32-CAM Dashboard..."

# Check if Python is installed
if command -v python3 &>/dev/null; then
    echo "Python is installed"
else
    echo "Python 3 not found. Please install Python 3 and try again."
    exit 1
fi

# Create virtual environment
echo "Creating virtual environment..."
python3 -m venv venv

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install required packages
echo "Installing required packages..."
pip install flask opencv-python numpy psutil requests pillow

echo "Setup complete!"
echo "To run the dashboard, use: python flask_webcam_dashboard.py"

# Ask if user wants to run the dashboard now
read -p "Do you want to run the dashboard now? (y/n): " choice
if [[ "$choice" == "y" || "$choice" == "Y" ]]; then
    python flask_webcam_dashboard.py
fi