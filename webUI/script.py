from flask import Flask, json, render_template, Response, jsonify, request, redirect, url_for, send_from_directory
import cv2
import time
import threading
import datetime
import psutil
import requests
import numpy as np
from urllib.request import urlopen
import io
import os
from PIL import Image
import base64

app = Flask(__name__)

# ESP32-CAM IP address / access points
ESP32_IP = "192.168.4.1"
STREAM_URL = f"http://{ESP32_IP}:81/stream"
CAPTURE_URL = f"http://{ESP32_IP}:80/capture"

# Notification Webhook URL
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1379740048760639530/oDc9nTxQEU5XqDFOTR1BTB8lHifTkHcVJHqqReImGFfsWiKIvnRTntAiNKC2caVy_RBW"

# Folder to store captured images
GALLERY_FOLDER = 'static/gallery'
os.makedirs(GALLERY_FOLDER, exist_ok=True)

# Global variables for stats
frame_count = 0
fps = 0
last_update_time = time.time()
connection_status = "Disconnected"
last_frame = None
frame_lock = threading.Lock()

# Connection management vars
max_retries = 3
retry_delay = 2
connection_timeout = 5

def send_discord_notification(image_path, filename, trigger_type="manual"):
    """
    Send a Discord notification with the captured image
    """
    try:
        # Skip if webhook not configured
        if not DISCORD_WEBHOOK_URL or "YOUR_WEBHOOK" in DISCORD_WEBHOOK_URL:
            print("Discord webhook not configured, skipping notification")
            return False
        
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Prepare the embed message
        embed = {
            "title": "🔔 ESP32-CAM Alert",
            "description": f"New image captured from ESP32-CAM",
            "color": 0x00ff00 if trigger_type == "doorbell" else 0x0099ff,
            "fields": [
                {
                    "name": "📅 Timestamp", 
                    "value": timestamp, 
                    "inline": True
                },
                {
                    "name": "Filename", 
                    "value": filename, 
                    "inline": True
                }
            ],
            "thumbnail": {
                "url": "attachment://image.jpg"
            },
            "footer": {
                "text": "ESP32-CAM Security System"
            }
        }
        
        # Prepare payload
        webhook_data = {
            "embeds": [embed]
        }
        
        # Read and prepare the image file
        with open(image_path, 'rb') as f:
            files = {
                'file': ('image.jpg', f, 'image/jpeg')
            }
            data = {
                'payload_json': json.dumps(webhook_data)
            }
            
            # Send
            response = requests.post(
                DISCORD_WEBHOOK_URL,
                data=data,
                files=files,
                timeout=10
            )
            
            if response.status_code == 204:
                print(f"Discord notification sent successfully for {filename}")
                return True
            else:
                print(f"Discord webhook failed: {response.status_code} - {response.text}")
                return False
                
    except Exception as e:
        print(f"Error sending Discord notification: {e}")
        return False

def test_esp32_connection():
    """Test if ESP32-CAM is reachable"""
    try:
        response = requests.get(f"http://{ESP32_IP}", timeout=connection_timeout)
        return response.status_code == 200
    except:
        return False

def get_camera_stream():
    """
    Generator function that continuously captures frames from the ESP32-CAM
    with improved error handling and connection management
    """
    global frame_count, fps, last_update_time, connection_status, last_frame
    
    print(f"Starting camera stream from {STREAM_URL}")
    
    retry_count = 0
    stream = None
    
    while retry_count < max_retries:
        try:
            # Test connection first
            if test_esp32_connection():
                connection_status = "Connected"
                break
            else:
                connection_status = f"Connection attempt {retry_count + 1}/{max_retries}"
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(retry_delay)
                    continue
                else:
                    connection_status = "Failed to connect"
                    print(f"Could not connect to ESP32-CAM at {ESP32_IP} after {max_retries} attempts")
        except Exception as e:
            connection_status = f"Connection error: {str(e)}"
            retry_count += 1
            if retry_count < max_retries:
                time.sleep(retry_delay)
            else:
                break
    
    # Try to open with OpenCV first
    if connection_status == "Connected":
        stream = cv2.VideoCapture(STREAM_URL)
        # Set buffer size to reduce latency
        stream.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    
    if stream is None or not stream.isOpened():
        print("Could not open stream with OpenCV, trying alternative method")
        # Alternative method using urllib
        try:
            while True:
                try:
                    if not test_esp32_connection():
                        connection_status = "Disconnected"
                        time.sleep(retry_delay)
                        continue
                    
                    connection_status = "Connected"
                    response = urlopen(STREAM_URL, timeout=connection_timeout)
                    
                    bytes_data = bytes()
                    while True:
                        chunk = response.read(1024)
                        if not chunk:
                            break
                        bytes_data += chunk
                        
                        a = bytes_data.find(b'\xff\xd8')  # JPEG start
                        b = bytes_data.find(b'\xff\xd9')  # JPEG end
                        
                        if a != -1 and b != -1:
                            jpg = bytes_data[a:b+2]
                            bytes_data = bytes_data[b+2:]
                            
                            # Convert bytes to numpy array
                            frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                            
                            if frame is not None:
                                # Update frame count and calculate FPS
                                frame_count += 1
                                current_time = time.time()
                                if current_time - last_update_time >= 1.0:
                                    fps = frame_count / (current_time - last_update_time)
                                    frame_count = 0
                                    last_update_time = current_time
                                
                                # Store latest frame with thread safety
                                with frame_lock:
                                    last_frame = frame.copy()
                                
                                # Convert frame to JPEG
                                ret, buffer = cv2.imencode('.jpg', frame)
                                if ret:
                                    frame_bytes = buffer.tobytes()
                                    
                                    # Yield the frame in the format expected by Flask
                                    yield (b'--frame\r\n'
                                          b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                except Exception as e:
                    connection_status = f"Stream error: {str(e)}"
                    print(f"Stream error: {e}")
                    time.sleep(retry_delay)
                    # Send placeholder image
                    try:
                        with open('static/no_signal.jpg', 'rb') as f:
                            placeholder = f.read()
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + placeholder + b'\r\n')
                    except:
                        pass
        except KeyboardInterrupt:
            pass
    else:
        # Using OpenCV capture with improved error handling
        print("Successfully opened stream with OpenCV")
        consecutive_failures = 0
        max_consecutive_failures = 10
        
        while True:
            try:
                success, frame = stream.read()
                if not success:
                    consecutive_failures += 1
                    connection_status = f"Stream issues ({consecutive_failures} failures)"
                    print(f"Failed to receive frame (attempt {consecutive_failures})")
                    
                    if consecutive_failures >= max_consecutive_failures:
                        print("Too many consecutive failures, attempting to reconnect...")
                        stream.release()
                        time.sleep(retry_delay)
                        
                        # Try to reconnect
                        if test_esp32_connection():
                            stream = cv2.VideoCapture(STREAM_URL)
                            stream.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                            consecutive_failures = 0
                        else:
                            connection_status = "Reconnection failed"
                    
                    # Send placeholder image
                    try:
                        with open('static/no_signal.jpg', 'rb') as f:
                            placeholder = f.read()
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + placeholder + b'\r\n')
                    except:
                        pass
                    
                    time.sleep(0.5)
                    continue
                
                # Reset failure counter on success
                consecutive_failures = 0
                connection_status = "Connected"
                
                # Update frame count and calculate FPS
                frame_count += 1
                current_time = time.time()
                if current_time - last_update_time >= 1.0:
                    fps = frame_count / (current_time - last_update_time)
                    frame_count = 0
                    last_update_time = current_time
                
                # Store latest frame with thread safety
                with frame_lock:
                    last_frame = frame.copy()
                
                # Convert frame to JPEG
                ret, buffer = cv2.imencode('.jpg', frame)
                if ret:
                    frame_bytes = buffer.tobytes()
                    
                    # Yield the frame in the format expected by Flask
                    yield (b'--frame\r\n'
                          b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                
            except Exception as e:
                consecutive_failures += 1
                connection_status = f"Error: {str(e)}"
                print(f"Stream error: {e}")
                time.sleep(retry_delay)
                
                # Send placeholder image
                try:
                    with open('static/no_signal.jpg', 'rb') as f:
                        placeholder = f.read()
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + placeholder + b'\r\n')
                except:
                    pass


def capture_image(trigger_type="manual"):
    """
    Capture an image from the ESP32-CAM and save it to the gallery
    """
    try:
        # Check connection status first
        if connection_status != "Connected":
            return None

        # Add cache busting parameter to force fresh capture
        import time
        cache_buster = int(time.time() * 1000)  # Current timestamp in milliseconds
        capture_url_with_cache_buster = f"{CAPTURE_URL}?t={cache_buster}"
        
        # Try to use the ESP32 capture endpoint with cache busting
        response = requests.get(capture_url_with_cache_buster, timeout=5)
        if response.status_code == 200:
            # Save the image with timestamp
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}.jpg"
            filepath = os.path.join(GALLERY_FOLDER, filename)
            
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            threading.Thread(
                target=send_discord_notification, 
                args=(filepath, filename, trigger_type),
                daemon=True
            ).start()
            return filename
        else:
            # If that fails, use the last captured frame
            with frame_lock:
                if last_frame is not None and connection_status == "Connected":
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"{timestamp}.jpg"
                    filepath = os.path.join(GALLERY_FOLDER, filename)
                    
                    cv2.imwrite(filepath, last_frame)

                    threading.Thread(
                        target=send_discord_notification, 
                        args=(filepath, filename, trigger_type),
                        daemon=True
                    ).start()
                    return filename
                else:
                    return None
                    
    except Exception as e:
        print(f"Error capturing image: {e}")
        # If the ESP32 capture fails, use the last captured frame
        with frame_lock:
            if last_frame is not None and connection_status == "Connected":
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{timestamp}.jpg"
                filepath = os.path.join(GALLERY_FOLDER, filename)
                
                cv2.imwrite(filepath, last_frame)
                return filename
            else:
                return None

def get_gallery_images():
    """Get list of images in the gallery sorted by date"""
    if not os.path.exists(GALLERY_FOLDER):
        return []
    
    images = [f for f in os.listdir(GALLERY_FOLDER) if f.endswith('.jpg')]
    images.sort(reverse=True) # sort by timestamp 
    
    return images

@app.route('/')
def index():
    """main dashboard page"""
    return render_template('base.html', active_page="stream")

@app.route('/stream')
def stream():
    """camera stream page"""
    return render_template('stream.html', active_page="stream")

@app.route('/gallery')
def gallery():
    """render the gallery page"""
    images = get_gallery_images()
    return render_template('gallery.html', images=images, active_page="gallery")

@app.route('/delete_images', methods=['POST'])
def delete_images():
    """API endpoint to delete one or multiple images"""
    try:
        # Get the list of images to delete from the request JSON
        data = request.get_json()
        if not data or 'images' not in data or not data['images']:
            return jsonify({'success': False, 'message': 'No images specified'})
        
        images_to_delete = data['images']
        deleted_count = 0
        
        # Validate and delete each image
        for image_name in images_to_delete:
            # Make sure the filename only contains safe characters
            if not all(c.isalnum() or c in '_-.' for c in image_name):
                continue
                
            # Get the full path and check that it exists
            image_path = os.path.join(GALLERY_FOLDER, image_name)
            if os.path.exists(image_path) and os.path.isfile(image_path):
                os.remove(image_path)
                deleted_count += 1
        
        return jsonify({
            'success': True, 
            'deleted_count': deleted_count,
            'message': f'Successfully deleted {deleted_count} image(s)'
        })
    except Exception as e:
        print(f"Error deleting images: {e}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/latest_image')
def latest_image():
    """API endpoint to get the latest image filename"""
    images = get_gallery_images()
    if images:
        return jsonify({'success': True, 'filename': images[0]})
    else:
        return jsonify({'success': False, 'message': 'No images found'})

@app.route('/doorbell')
def doorbell():
    """Render the doorbell page"""
    return render_template('doorbell.html', active_page="doorbell")

@app.route('/video_feed')
def video_feed():
    """Route for streaming video"""
    return Response(get_camera_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/capture', methods=['POST', 'GET'])  # Added GET method for compatibility
def capture():
    """API endpoint to capture an image"""
    # Handle both JSON and form data
    trigger_type = "manual"
    
    if request.method == 'POST':
        if request.is_json:
            data = request.get_json() or {}
            trigger_type = data.get('trigger_type', 'manual')
        else:
            # Handle form data or query parameters
            trigger_type = request.form.get('trigger_type', request.args.get('trigger_type', 'manual'))
    else:  # GET method
        trigger_type = request.args.get('trigger_type', 'manual')
    
    filename = capture_image(trigger_type)
    if filename:
        return jsonify({'success': True, 'filename': filename})
    else:
        return jsonify({'success': False, 'message': 'Failed to capture image'})

@app.route('/stats')
def stats():
    """API endpoint to get current statistics"""
    # Get host system stats
    cpu_percent = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    memory_percent = memory.percent
    
    # Get current time 
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Create stats object
    stats_data = {
        'fps': round(fps, 1),
        'connection_status': connection_status,
        'cpu_percent': cpu_percent,
        'memory_percent': memory_percent,
        'current_time': current_time,
        'gallery_count': len(get_gallery_images()),
        'esp32_reachable': test_esp32_connection()
    }
    
    # If we have a frame, add resolution info
    if last_frame is not None:
        with frame_lock:
            height, width = last_frame.shape[:2]
        stats_data['resolution'] = f"{width}x{height}"
    else:
        stats_data['resolution'] = "Unknown"
    
    return jsonify(stats_data)

@app.route('/favicon.ico')
def favicon():
    """Serve favicon to prevent 404 errors"""
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

def create_required_files():
    """Create necessary static files and templates for the app"""
    # Create directories if they don't exist
    if not os.path.exists('templates'):
        os.makedirs('templates')
    if not os.path.exists('static'):
        os.makedirs('static')
    if not os.path.exists(GALLERY_FOLDER):
        os.makedirs(GALLERY_FOLDER)
    
    # Create a placeholder no signal image
    img = Image.new('RGB', (640, 480), color=(20, 20, 20))
    img_io = io.BytesIO()
    img.save(img_io, 'JPEG')
    img_io.seek(0)
    
    with open('static/no_signal.jpg', 'wb') as f:
        f.write(img_io.getvalue())
    
    # Create bell icon 
    with open('static/doorbell.svg', 'w') as f:
        f.write('''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="feather feather-bell"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path><path d="M13.73 21a2 2 0 0 1-3.46 0"></path></svg>''')
    
    # Create a simple favicon
    favicon_img = Image.new('RGB', (16, 16), color=(0, 100, 200))
    favicon_img.save('static/favicon.ico', 'ICO')

if __name__ == '__main__':
    # Create required files and directories
    create_required_files()
    
    # Start the Flask web server
    print("Starting ESP32-CAM Security System web server on http://0.0.0.0:5000")
    print("Make sure your ESP32-CAM is connected and accessible at 192.168.4.1")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)