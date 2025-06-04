# esp32-webcam-server
esp32 webcam server with flask frontend

## Setup Instructions
1. Instal all necessary Python and Arduino Libraries.
2. Setup Arduino board with correct COM Port in the Arduino IDE
3. Compile and upload the WebcamServer.ino file to the ESP32 Board
4. Navigate to the webUI folder
5. Start the python Flask app
6. `/webUI>python -m script.py`
7. Go to `127.0.0.1:5000`

## Project Overview
`/esp32/`
- Contains the Webserver file for the ESP32 Board
`/esp32/WebcamServer.ino`
- Code for the ESP32 Server which is accessible using it's AccessPoint configuration. It has multiple endpoints for image capture and livestream.

`/webUI/`
- Contains the Flask webserver and User Interface as well as static files and stored images.
`/webUI/static/`
- Contains static files such as icons and saved images.
`/webUI/static/gallery/`
- Stores the captured images from the doorbell.
`/webUI/templates/`
- Hosts HTML templates used for the frontend.
`/webUI/templates/base.html`
- Layout for all pages
The rest of the template pages are self explainatory

`/webUI/script.py/`
- Contains all the routes and endpoints for the frontend, handles communication with the esp32 board.