import eventlet
eventlet.monkey_patch()

import os
import time
import json
import hashlib
from flask import Flask, request, session, redirect, url_for, render_template_string, jsonify
from flask_socketio import SocketIO, emit
from flask_socketio import disconnect as server_disconnect_client
import traceback
import sys
import logging
from datetime import datetime, timedelta
import threading
from collections import defaultdict, deque
import gzip
import io

# --- Enhanced Logging Setup ---
log_format = '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- Enhanced Configuration ---
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'change_this_strong_secret_key_12345_server_v3')
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', '1')
ENABLE_COMPRESSION = os.environ.get('ENABLE_COMPRESSION', 'true').lower() == 'true'
MAX_CONNECTIONS = int(os.environ.get('MAX_CONNECTIONS', '10'))
ENABLE_METRICS = os.environ.get('ENABLE_METRICS', 'true').lower() == 'true'
ENABLE_RATE_LIMITING = os.environ.get('ENABLE_RATE_LIMITING', 'true').lower() == 'true'

# --- Performance Monitoring ---
class PerformanceMonitor:
    def __init__(self):
        self.metrics = defaultdict(lambda: {
            'count': 0,
            'total_time': 0,
            'avg_time': 0,
            'max_time': 0,
            'min_time': float('inf')
        })
        self.frame_times = deque(maxlen=100)
        self.connection_stats = {
            'total_connections': 0,
            'active_connections': 0,
            'peak_connections': 0,
            'total_frames_sent': 0,
            'total_commands_processed': 0
        }
        self.start_time = time.time()
        self.lock = threading.Lock()

    def record_frame_time(self, frame_time):
        with self.lock:
            self.frame_times.append(frame_time)
            self.connection_stats['total_frames_sent'] += 1

    def record_metric(self, name, duration):
        with self.lock:
            metric = self.metrics[name]
            metric['count'] += 1
            metric['total_time'] += duration
            metric['avg_time'] = metric['total_time'] / metric['count']
            metric['max_time'] = max(metric['max_time'], duration)
            metric['min_time'] = min(metric['min_time'], duration)

    def get_stats(self):
        with self.lock:
            uptime = time.time() - self.start_time
            avg_frame_time = sum(self.frame_times) / len(self.frame_times) if self.frame_times else 0
            return {
                'uptime_seconds': uptime,
                'connection_stats': self.connection_stats,
                'average_frame_processing_ms': avg_frame_time * 1000,
                'recent_frame_times': list(self.frame_times)[-10:],
                'metrics': dict(self.metrics)
            }

# --- Rate Limiting ---
class RateLimiter:
    def __init__(self, max_requests=100, window_seconds=60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = defaultdict(deque)
        self.lock = threading.Lock()

    def is_allowed(self, identifier):
        if not ENABLE_RATE_LIMITING:
            return True
            
        now = time.time()
        with self.lock:
            # Clean old requests
            while self.requests[identifier] and self.requests[identifier][0] < now - self.window_seconds:
                self.requests[identifier].popleft()
            
            # Check if under limit
            if len(self.requests[identifier]) < self.max_requests:
                self.requests[identifier].append(now)
                return True
            return False

# Global instances
perf_monitor = PerformanceMonitor()
rate_limiter = RateLimiter()

# --- Enhanced Flask App Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
app.config['COMPRESS_MIMETYPES'] = [
    'text/html', 'text/css', 'text/xml', 'application/json',
    'application/javascript', 'text/javascript'
]

# Enhanced SocketIO with better configuration
socketio = SocketIO(
    app, 
    async_mode='eventlet',
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=10 * 1024 * 1024,  # Reduced for better performance
    logger=False,
    engineio_logger=False,
    cors_allowed_origins="*",
    compression=ENABLE_COMPRESSION
)

# --- Global Variables ---
client_pc_sid = None
connected_clients = {}
frame_cache = {}
last_frame_hash = None

# --- Enhanced Authentication ---
def check_auth(password):
    return password == ACCESS_PASSWORD

def get_client_ip():
    """Get client IP address, handling proxies"""
    if request.environ.get('HTTP_X_FORWARDED_FOR'):
        return request.environ['HTTP_X_FORWARDED_FOR'].split(',')[0].strip()
    elif request.environ.get('HTTP_X_REAL_IP'):
        return request.environ['HTTP_X_REAL_IP']
    else:
        return request.environ.get('REMOTE_ADDR', 'unknown')

# --- Health Check Endpoint ---
@app.route('/health')
def health_check():
    """Health check endpoint for Render.com"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'uptime_seconds': time.time() - perf_monitor.start_time,
        'active_connections': perf_monitor.connection_stats['active_connections'],
        'client_connected': client_pc_sid is not None
    }), 200

# --- Metrics Endpoint ---
@app.route('/metrics')
def metrics():
    """Metrics endpoint for monitoring"""
    if not ENABLE_METRICS:
        return jsonify({'error': 'Metrics disabled'}), 404
    
    return jsonify(perf_monitor.get_stats())

# --- Enhanced Routes ---
@app.route('/', methods=['GET', 'POST'])
def index():
    client_ip = get_client_ip()
    
    if request.method == 'POST':
        if not rate_limiter.is_allowed(f"login_{client_ip}"):
            return render_template_string(LOGIN_HTML, error="Too many login attempts. Please try again later.")
        
        password = request.form.get('password')
        if check_auth(password):
            session['authenticated'] = True
            session['login_time'] = time.time()
            logger.info(f"Successful login from {client_ip}")
            return redirect(url_for('interface'))
        else:
            logger.warning(f"Failed login attempt from {client_ip}")
            return render_template_string(LOGIN_HTML, error="Invalid password")
    
    if session.get('authenticated'):
        return redirect(url_for('interface'))
    return render_template_string(LOGIN_HTML)

@app.route('/interface')
def interface():
    if not session.get('authenticated'):
        return redirect(url_for('index'))
    
    # Check session timeout (4 hours)
    if time.time() - session.get('login_time', 0) > 14400:
        session.pop('authenticated', None)
        return redirect(url_for('index'))
    
    return render_template_string(ENHANCED_INTERFACE_HTML)

@app.route('/logout')
def logout():
    client_ip = get_client_ip()
    logger.info(f"Logout from {client_ip}")
    session.pop('authenticated', None)
    return redirect(url_for('index'))

# --- Enhanced SocketIO Events ---
@socketio.on('connect')
def handle_connect():
    client_ip = get_client_ip()
    
    if not rate_limiter.is_allowed(f"connect_{client_ip}"):
        logger.warning(f"Rate limited connection from {client_ip}")
        return False
    
    perf_monitor.connection_stats['total_connections'] += 1
    perf_monitor.connection_stats['active_connections'] += 1
    perf_monitor.connection_stats['peak_connections'] = max(
        perf_monitor.connection_stats['peak_connections'],
        perf_monitor.connection_stats['active_connections']
    )
    
    connected_clients[request.sid] = {
        'ip': client_ip,
        'connect_time': time.time(),
        'type': 'unknown'
    }
    
    logger.info(f"Client connected - SID: {request.sid}, IP: {client_ip}")

@socketio.on('disconnect')
def handle_disconnect():
    global client_pc_sid
    
    perf_monitor.connection_stats['active_connections'] -= 1
    
    if request.sid in connected_clients:
        client_info = connected_clients.pop(request.sid)
        logger.info(f"Client disconnected - SID: {request.sid}, IP: {client_info['ip']}")
    
    if request.sid == client_pc_sid:
        logger.warning(f"Remote PC (SID: {client_pc_sid}) disconnected.")
        client_pc_sid = None
        emit('client_disconnected', {'message': 'Remote PC disconnected.'}, broadcast=True, include_self=False)

@socketio.on('register_client')
def handle_register_client(data):
    global client_pc_sid
    client_token = data.get('token')
    sid = request.sid
    client_ip = get_client_ip()
    
    if not rate_limiter.is_allowed(f"register_{client_ip}"):
        emit('registration_fail', {'message': 'Rate limited'}, room=sid)
        server_disconnect_client(sid)
        return
    
    if client_token == ACCESS_PASSWORD:
        if client_pc_sid and client_pc_sid != sid:
            try:
                server_disconnect_client(client_pc_sid, silent=True)
            except Exception as e:
                logger.error(f"Error disconnecting old client {client_pc_sid}: {e}")
        
        client_pc_sid = sid
        connected_clients[sid]['type'] = 'pc_client'
        
        logger.info(f"Remote PC registered - SID: {sid}, IP: {client_ip}")
        emit('client_connected', {'message': 'Remote PC connected.'}, broadcast=True, include_self=False)
        emit('registration_success', room=sid)
    else:
        logger.warning(f"Failed registration attempt from {client_ip}")
        emit('registration_fail', {'message': 'Auth failed.'}, room=sid)
        server_disconnect_client(sid)

@socketio.on('screen_data_bytes')
def handle_screen_data_bytes(data):
    global last_frame_hash
    
    if request.sid != client_pc_sid or not data or not isinstance(data, bytes):
        return
    
    start_time = time.time()
    
    # Frame deduplication
    frame_hash = hashlib.md5(data).hexdigest()
    if frame_hash == last_frame_hash:
        return  # Skip duplicate frames
    
    last_frame_hash = frame_hash
    
    # Compress frame if enabled
    if ENABLE_COMPRESSION and len(data) > 1024:
        try:
            compressed = gzip.compress(data, compresslevel=1)  # Fast compression
            if len(compressed) < len(data) * 0.9:  # Only use if significant compression
                emit('screen_frame_compressed', compressed, broadcast=True, include_self=False)
            else:
                emit('screen_frame_bytes', data, broadcast=True, include_self=False)
        except Exception as e:
            logger.error(f"Compression error: {e}")
            emit('screen_frame_bytes', data, broadcast=True, include_self=False)
    else:
        emit('screen_frame_bytes', data, broadcast=True, include_self=False)
    
    processing_time = time.time() - start_time
    perf_monitor.record_frame_time(processing_time)
    perf_monitor.record_metric('frame_processing', processing_time)

@socketio.on('control_command')
def handle_control_command(data):
    if not session.get('authenticated'):
        return
    
    client_ip = get_client_ip()
    if not rate_limiter.is_allowed(f"command_{client_ip}"):
        emit('command_error', {'message': 'Rate limited'}, room=request.sid)
        return
    
    if client_pc_sid:
        start_time = time.time()
        emit('command', data, room=client_pc_sid)
        perf_monitor.connection_stats['total_commands_processed'] += 1
        perf_monitor.record_metric('command_processing', time.time() - start_time)
    else:
        emit('command_error', {'message': 'Remote PC not connected.'}, room=request.sid)

@socketio.on('set_injection_text')
def handle_set_injection_text(data):
    if not session.get('authenticated'):
        return
    
    text_to_inject = data.get('text_to_inject')
    if client_pc_sid:
        if text_to_inject is not None:
            logger.info(f"Text injection sent to SID {client_pc_sid}")
            emit('receive_injection_text', {'text': text_to_inject}, room=client_pc_sid)
            emit('text_injection_set_ack', {'status': 'success'}, room=request.sid)
        else:
            emit('text_injection_set_ack', {'status': 'error', 'message': 'No text data received.'}, room=request.sid)
    else:
        emit('text_injection_set_ack', {'status': 'error', 'message': 'Remote PC not connected.'}, room=request.sid)

# --- Enhanced HTML Templates ---
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remote Control - Login</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style> 
        body { font-family: 'Inter', sans-serif; }
        .fade-in { animation: fadeIn 0.5s ease-in; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body class="bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center min-h-screen">
    <div class="bg-white p-8 rounded-xl shadow-lg w-full max-w-md fade-in">
        <div class="text-center mb-6">
            <h1 class="text-3xl font-bold text-gray-800 mb-2">Remote Desktop</h1>
            <p class="text-gray-600">Secure remote access</p>
        </div>
        
        {% if error %}
            <div class="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-4 text-sm">
                {{ error }}
            </div>
        {% endif %}
        
        <form method="POST" action="{{ url_for('index') }}" class="space-y-4">
            <div>
                <label for="password" class="block text-gray-700 text-sm font-medium mb-2">Access Password</label>
                <input type="password" id="password" name="password" required
                       class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-colors"
                       placeholder="Enter your password">
            </div>
            <button type="submit"
                    class="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-3 px-4 rounded-lg transition duration-200 ease-in-out transform hover:scale-105">
                Access Remote Desktop
            </button>
        </form>
        
        <div class="mt-6 text-center text-xs text-gray-500">
            <p>Secure connection • End-to-end encrypted</p>
        </div>
    </div>
</body>
</html>
"""

ENHANCED_INTERFACE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remote Desktop Control</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/pako/2.1.0/pako.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        html, body { 
            height: 100%; 
            overflow: hidden; 
            font-family: 'Inter', sans-serif; 
            margin: 0; 
            padding: 0; 
            box-sizing: border-box; 
        }
        
        #main-content { 
            display: flex; 
            flex-direction: column; 
            height: calc(100% - 4rem); 
        }
        
        #screen-view-area { 
            flex-grow: 1; 
            display: flex; 
            align-items: center; 
            justify-content: center; 
            background: linear-gradient(135deg, #1e293b 0%, #334155 100%); 
            overflow: hidden; 
            position: relative; 
            transition: height 0.3s ease-in-out; 
        }
        
        #text-input-area { 
            height: 0; 
            overflow: hidden; 
            background-color: #f8fafc; 
            padding: 0; 
            transition: height 0.3s ease-in-out, padding 0.3s ease-in-out; 
            display: flex; 
            flex-direction: column; 
            border-top: 1px solid #e2e8f0;
        }
        
        body.text-input-mode #screen-view-area { height: 50%; }
        body.text-input-mode #text-input-area { height: 50%; padding: 1rem; }

        #screen-image { 
            max-width: 100%; 
            max-height: 100%; 
            height: auto; 
            width: auto; 
            display: block; 
            cursor: crosshair; 
            object-fit: contain; 
            border-radius: 8px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.2);
        }
        
        .status-dot { 
            height: 8px; 
            width: 8px; 
            border-radius: 50%; 
            display: inline-block; 
            margin-right: 8px; 
            animation: pulse 2s infinite;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        .status-connected { background: linear-gradient(45deg, #10b981, #059669); }
        .status-disconnected { background: linear-gradient(45deg, #ef4444, #dc2626); }
        .status-connecting { background: linear-gradient(45deg, #f59e0b, #d97706); }
        
        .control-button {
            padding: 0.75rem 1.5rem;
            background: linear-gradient(45deg, #3b82f6, #2563eb);
            color: white; 
            border: none;
            border-radius: 0.5rem; 
            cursor: pointer; 
            transition: all 0.2s;
            margin-right: 0.5rem;
            font-weight: 500;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        
        .control-button:hover { 
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(0,0,0,0.15);
        }
        
        .control-button.active { 
            background: linear-gradient(45deg, #10b981, #059669); 
        }
        
        .stats-panel {
            position: absolute;
            top: 1rem;
            right: 1rem;
            background: rgba(0,0,0,0.8);
            color: white;
            padding: 0.75rem;
            border-radius: 0.5rem;
            font-size: 0.75rem;
            font-family: monospace;
            backdrop-filter: blur(10px);
            display: none;
        }
        
        .stats-panel.visible {
            display: block;
        }
        
        .performance-indicator {
            display: inline-flex;
            align-items: center;
            background: rgba(0,0,0,0.1);
            padding: 0.25rem 0.5rem;
            border-radius: 0.25rem;
            margin-left: 0.5rem;
            font-size: 0.75rem;
        }
        
        .latency-good { color: #10b981; }
        .latency-medium { color: #f59e0b; }
        .latency-poor { color: #ef4444; }
    </style>
</head>
<body class="bg-gray-50 flex flex-col h-screen" tabindex="0">

    <header class="bg-gradient-to-r from-slate-800 to-slate-900 text-white p-4 flex justify-between items-center shadow-lg flex-shrink-0 h-16">
        <div class="flex items-center">
            <h1 class="text-xl font-bold">Remote Desktop Pro</h1>
            <div class="performance-indicator">
                <span id="latency-indicator" class="latency-good">●</span>
                <span id="latency-text">0ms</span>
            </div>
        </div>
        
        <div class="flex items-center space-x-4">
            <button id="stats-toggle" class="control-button text-sm">Stats</button>
            <button id="toggle-text-mode-button" class="control-button text-sm">Text Mode</button>
            <div id="connection-status" class="flex items-center text-sm">
                <span id="status-dot" class="status-dot status-connecting"></span>
                <span id="status-text">Connecting...</span>
            </div>
            <a href="{{ url_for('logout') }}" class="bg-red-600 hover:bg-red-700 text-white text-sm font-medium py-2 px-4 rounded-lg transition duration-150 ease-in-out">
                Logout
            </a>
        </div>
    </header>

    <main id="main-content" class="p-4">
        <div id="screen-view-area">
            <img id="screen-image" 
                 src="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iODAiIGhlaWdodD0iODAiIHZpZXdCb3g9IjAgMCA4MCA4MCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPGNpcmNsZSBjeD0iNDAiIGN5PSI0MCIgcj0iNDAiIGZpbGw9IiNGM0Y0RjYiLz4KPHBhdGggZD0iTTQwIDIwVjYwTTIwIDQwSDYwIiBzdHJva2U9IiM2QjdCODAiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+CjwvZz4KPC9zdmc+"
                 alt="Waiting for remote screen..."
                 class="opacity-50">
            
            <div id="stats-panel" class="stats-panel">
                <div id="stats-content">
                    <div>FPS: <span id="fps-counter">0</span></div>
                    <div>Latency: <span id="latency-ms">0ms</span></div>
                    <div>Frames: <span id="frame-counter">0</span></div>
                    <div>Quality: <span id="quality-indicator">Auto</span></div>
                </div>
            </div>
        </div>
        
        <div id="text-input-area">
            <textarea id="injection-text" 
                      class="w-full flex-grow p-4 border border-gray-300 rounded-lg font-mono text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      placeholder="Enter text to inject... Press F1 on remote PC to type this text."></textarea>
            <div class="mt-4 flex justify-between items-center">
                <p id="injection-status" class="text-sm text-green-600"></p>
                <button id="send-injection-text-button" class="control-button">
                    Save Text for Injection
                </button>
            </div>
        </div>
    </main>

    <script>
        class RemoteDesktopController {
            constructor() {
                this.socket = null;
                this.remoteScreenWidth = null;
                this.remoteScreenHeight = null;
                this.currentImageUrl = null;
                this.isTextModeActive = false;
                this.statsVisible = false;
                this.frameCount = 0;
                this.lastFrameTime = Date.now();
                this.latencyTimes = [];
                this.commandSentTime = null;
                
                this.initializeElements();
                this.setupEventListeners();
                this.connectToServer();
            }
            
            initializeElements() {
                this.screenImage = document.getElementById('screen-image');
                this.statusDot = document.getElementById('status-dot');
                this.statusText = document.getElementById('status-text');
                this.toggleTextModeButton = document.getElementById('toggle-text-mode-button');
                this.injectionTextarea = document.getElementById('injection-text');
                this.sendInjectionTextButton = document.getElementById('send-injection-text-button');
                this.injectionStatus = document.getElementById('injection-status');
                this.statsToggle = document.getElementById('stats-toggle');
                this.statsPanel = document.getElementById('stats-panel');
                this.fpsCounter = document.getElementById('fps-counter');
                this.latencyIndicator = document.getElementById('latency-indicator');
                this.latencyText = document.getElementById('latency-text');
                this.frameCounter = document.getElementById('frame-counter');
            }
            
            setupEventListeners() {
                // Stats toggle
                this.statsToggle.addEventListener('click', () => this.toggleStats());
                
                // Text mode toggle
                this.toggleTextModeButton.addEventListener('click', () => this.toggleTextMode());
                
                // Text injection
                this.sendInjectionTextButton.addEventListener('click', () => this.sendInjectionText());
                
                // Keyboard and mouse events
                this.setupInputHandlers();
            }
            
            connectToServer() {
                this.socket = io(window.location.origin, { 
                    path: '/socket.io/',
                    transports: ['websocket'],
                    upgrade: true,
                    rememberUpgrade: true
                });
                
                this.setupSocketEvents();
            }
            
            setupSocketEvents() {
                this.socket.on('connect', () => {
                    this.updateStatus('status-connecting', 'Server connected, waiting for PC...');
                });
                
                this.socket.on('disconnect', () => {
                    this.updateStatus('status-disconnected', 'Server disconnected');
                    this.cleanup();
                });
                
                this.socket.on('client_connected', () => {
                    this.updateStatus('status-connected', 'Remote PC Connected');
                    document.body.focus();
                });
                
                this.socket.on('client_disconnected', () => {
                    this.updateStatus('status-disconnected', 'Remote PC Disconnected');
                    this.cleanup();
                });
                
                this.socket.on('screen_frame_bytes', (imageDataBytes) => {
                    this.handleScreenFrame(imageDataBytes);
                });
                
                this.socket.on('screen_frame_compressed', (compressedData) => {
                    try {
                        const decompressed = pako.ungzip(compressedData);
                        this.handleScreenFrame(decompressed);
                    } catch (error) {
                        console.error('Decompression error:', error);
                    }
                });
                
                this.socket.on('text_injection_set_ack', (data) => {
                    this.handleInjectionAck(data);
                });
            }
            
            handleScreenFrame(imageDataBytes) {
                const now = Date.now();
                this.frameCount++;
                
                // Calculate FPS
                if (now - this.lastFrameTime > 1000) {
                    const fps = this.frameCount / ((now - this.lastFrameTime) / 1000);
                    this.fpsCounter.textContent = fps.toFixed(1);
                    this.frameCount = 0;
                    this.lastFrameTime = now;
                }
                
                // Update frame counter
                this.frameCounter.textContent = parseInt(this.frameCounter.textContent) + 1;
                
                // Create and display image
                const blob = new Blob([imageDataBytes], { type: 'image/jpeg' });
                const newImageUrl = URL.createObjectURL(blob);
                
                // Detect screen dimensions
                if (this.remoteScreenWidth === null) {
                    const tempImg = new Image();
                    tempImg.onload = () => {
                        this.remoteScreenWidth = tempImg.naturalWidth;
                        this.remoteScreenHeight = tempImg.naturalHeight;
                        URL.revokeObjectURL(tempImg.src);
                    };
                    tempImg.src = newImageUrl;
                }
                
                // Update image
                const oldUrl = this.currentImageUrl;
                this.currentImageUrl = newImageUrl;
                this.screenImage.onload = () => {
                    if (oldUrl) URL.revokeObjectURL(oldUrl);
                };
                this.screenImage.src = newImageUrl;
            }
            
            setupInputHandlers() {
                // Mouse events
                this.screenImage.addEventListener('mousemove', (event) => {
                    this.handleMouseMove(event);
                });
                
                this.screenImage.addEventListener('click', (event) => {
                    this.handleMouseClick(event, 'left');
                });
                
                this.screenImage.addEventListener('contextmenu', (event) => {
                    event.preventDefault();
                    this.handleMouseClick(event, 'right');
                });
                
                this.screenImage.addEventListener('wheel', (event) => {
                    this.handleMouseWheel(event);
                });
                
                // Keyboard events
                document.body.addEventListener('keydown', (event) => {
                    this.handleKeyDown(event);
                });
                
                document.body.addEventListener('keyup', (event) => {
                    this.handleKeyUp(event);
                });
            }
            
            handleMouseMove(event) {
                if (!this.remoteScreenWidth) return;
                
                const rect = this.screenImage.getBoundingClientRect();
                const x = event.clientX - rect.left;
                const y = event.clientY - rect.top;
                const remoteX = Math.round((x / rect.width) * this.remoteScreenWidth);
                const remoteY = Math.round((y / rect.height) * this.remoteScreenHeight);
                
                this.sendCommand({ action: 'move', x: remoteX, y: remoteY });
            }
            
            handleMouseClick(event, button) {
                if (!this.remoteScreenWidth) return;
                
                const rect = this.screenImage.getBoundingClientRect();
                const x = event.clientX - rect.left;
                const y = event.clientY - rect.top;
                const remoteX = Math.round((x / rect.width) * this.remoteScreenWidth);
                const remoteY = Math.round((y / rect.height) * this.remoteScreenHeight);
                
                this.sendCommand({ action: 'click', button: button, x: remoteX, y: remoteY });
                document.body.focus();
            }
            
            handleMouseWheel(event) {
                event.preventDefault();
                const dY = event.deltaY > 0 ? 1 : (event.deltaY < 0 ? -1 : 0);
                if (dY) {
                    this.sendCommand({ action: 'scroll', dx: 0, dy: dY });
                }
                document.body.focus();
            }
            
            handleKeyDown(event) {
                if (this.isTextInputFocused()) return;
                
                // Prevent default for most keys
                if (this.shouldPreventDefault(event)) {
                    event.preventDefault();
                }
                
                const command = {
                    action: 'keydown',
                    key: event.key,
                    code: event.code,
                    ctrlKey: event.ctrlKey,
                    shiftKey: event.shiftKey,
                    altKey: event.altKey,
                    metaKey: event.metaKey
                };
                
                this.sendCommand(command);
            }
            
            handleKeyUp(event) {
                if (this.isTextInputFocused()) return;
                
                const command = {
                    action: 'keyup',
                    key: event.key,
                    code: event.code
                };
                
                this.sendCommand(command);
            }
            
            sendCommand(command) {
                this.commandSentTime = Date.now();
                this.socket.emit('control_command', command);
            }
            
            sendInjectionText() {
                const text = this.injectionTextarea.value;
                this.socket.emit('set_injection_text', { text_to_inject: text });
                this.injectionStatus.textContent = 'Saving...';
            }
            
            handleInjectionAck(data) {
                this.injectionStatus.textContent = data.status === 'success' 
                    ? 'Text saved successfully!' 
                    : `Error: ${data.message || 'Failed to save'}`;
                
                setTimeout(() => {
                    this.injectionStatus.textContent = '';
                }, 3000);
            }
            
            toggleTextMode() {
                this.isTextModeActive = !this.isTextModeActive;
                document.body.classList.toggle('text-input-mode', this.isTextModeActive);
                
                if (this.isTextModeActive) {
                    this.toggleTextModeButton.textContent = 'Screen Mode';
                    this.toggleTextModeButton.classList.add('active');
                    this.injectionTextarea.focus();
                } else {
                    this.toggleTextModeButton.textContent = 'Text Mode';
                    this.toggleTextModeButton.classList.remove('active');
                    document.body.focus();
                }
            }
            
            toggleStats() {
                this.statsVisible = !this.statsVisible;
                this.statsPanel.classList.toggle('visible', this.statsVisible);
                this.statsToggle.classList.toggle('active', this.statsVisible);
            }
            
            updateStatus(statusClass, message) {
                this.statusText.textContent = message;
                this.statusDot.className = `status-dot ${statusClass}`;
            }
            
            isTextInputFocused() {
                return document.activeElement === this.injectionTextarea;
            }
            
            shouldPreventDefault(event) {
                // Logic to determine if we should prevent default browser behavior
                const specialKeys = ['Tab', 'Enter', 'Escape', 'Backspace', 'Delete', 'Insert', 'Home', 'End', 'PageUp', 'PageDown'];
                const arrowKeys = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];
                const functionKeys = event.key.startsWith('F') && event.key.length > 1;
                
                return specialKeys.includes(event.key) || 
                       arrowKeys.includes(event.key) || 
                       functionKeys ||
                       (event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey);
            }
            
            cleanup() {
                if (this.currentImageUrl) {
                    URL.revokeObjectURL(this.currentImageUrl);
                    this.currentImageUrl = null;
                }
                this.remoteScreenWidth = null;
                this.remoteScreenHeight = null;
            }
        }
        
        // Initialize the controller when DOM is loaded
        document.addEventListener('DOMContentLoaded', () => {
            new RemoteDesktopController();
        });
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    logger.info("--- Enhanced Remote Desktop Server ---")
    port = int(os.environ.get('PORT', 5000))
    host = '0.0.0.0'
    
    logger.info(f"Starting server on http://{host}:{port}")
    logger.info(f"Compression: {ENABLE_COMPRESSION}")
    logger.info(f"Rate limiting: {ENABLE_RATE_LIMITING}")
    logger.info(f"Metrics: {ENABLE_METRICS}")
    
    if ACCESS_PASSWORD == '1':
        logger.warning("⚠️  USING DEFAULT PASSWORD '1' - CHANGE THIS IN PRODUCTION!")
    
    try:
        socketio.run(app, host=host, port=port, debug=False)
    except Exception as e:
        logger.critical(f"Failed to start server: {e}")
        sys.exit(1)
