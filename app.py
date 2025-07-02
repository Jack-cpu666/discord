import os
import time
import redis
import sys
import logging
import threading
import base64
import asyncio
import aiohttp
import json
from urllib.parse import urlparse

# --- Logging Setup ---
log_format = '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- HARDCODED Configuration ---
SECRET_KEY = 'super_secret_flask_key_12345_hardcoded'
ACCESS_PASSWORD = 'mypassword123'  # Change this to your desired password
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')  # Will use Render's Redis if available
ANTHROPIC_API_KEY = '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'  # Your API key

# --- Critical Redis Setup BEFORE any imports that might monkey patch ---
if not REDIS_URL:
    logger.critical("FATAL: REDIS_URL environment variable not set! The server cannot run without it.")
    sys.exit(1)

# Parse Redis URL to get connection details
redis_url_parsed = urlparse(REDIS_URL)
redis_host = redis_url_parsed.hostname
redis_port = redis_url_parsed.port or 6379
redis_password = redis_url_parsed.password
redis_db = int(redis_url_parsed.path.lstrip('/')) if redis_url_parsed.path else 0

# Use standard socket DNS resolution to avoid eventlet interference
import socket
redis_ip = None
try:
    redis_ip = socket.gethostbyname(redis_host)
    logger.info(f"Resolved Redis host {redis_host} to IP {redis_ip}")
except Exception as e:
    logger.critical(f"FATAL: Could not resolve Redis hostname {redis_host}: {e}")
    sys.exit(1)

# Create Redis connection pool with IP address instead of hostname
redis_connection_pool = redis.ConnectionPool(
    host=redis_ip,
    port=redis_port,
    password=redis_password,
    db=redis_db,
    decode_responses=True,
    socket_connect_timeout=10,
    socket_timeout=10,
    retry_on_timeout=True,
    health_check_interval=30
)

# Test initial connection
try:
    redis_client = redis.Redis(connection_pool=redis_connection_pool)
    redis_client.ping()
    logger.info("Successfully connected to Redis at startup using IP address.")
except redis.exceptions.ConnectionError as e:
    logger.critical(f"FATAL: Could not connect to Redis at startup: {e}")
    sys.exit(1)

# NOW it's safe to monkey patch after Redis connection is established
import eventlet
eventlet.monkey_patch()

# Import Flask after monkey patching
from flask import Flask, request, session, redirect, url_for, render_template_string
from flask_socketio import SocketIO, emit
from flask_socketio import disconnect as server_disconnect_client

# --- App Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB upload limit
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", ping_timeout=90, ping_interval=30,
                    max_http_buffer_size=20 * 1024 * 1024, logger=False, engineio_logger=False)

# --- Redis Key Management ---
CLIENT_REDIS_KEY = f"remote_client_sid:{ACCESS_PASSWORD}"
AI_ENABLED_KEY = f"ai_enabled:{ACCESS_PASSWORD}"
AI_ANSWER_KEY = f"ai_answer:{ACCESS_PASSWORD}"

# --- Claude AI Configuration ---
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-3-5-sonnet-20241022"

SYSTEM_PROMPT = """You are an AI assistant that analyzes screenshots to help answer questions. 

CRITICAL INSTRUCTIONS:
1. Look at the screenshot carefully
2. Identify any questions, problems, or tasks shown
3. Provide ONLY the direct answer - no explanations, no reasoning, no additional text
4. Format your response as: answer=[your answer here]
5. If it's a multiple choice question, give the letter/option (e.g., answer=B)
6. If it's a math problem, give the number (e.g., answer=42)
7. If it's a short answer, give the exact answer (e.g., answer=photosynthesis)
8. If it's an essay question or requires a long response, write the full response after the equals sign

Examples:
- For "What is 2+2?" respond: answer=4
- For "Which is correct? A) Dog B) Cat" respond: answer=A
- For "Explain photosynthesis" respond: answer=Photosynthesis is the process by which plants convert sunlight into energy...

Be precise and direct. Only provide what is asked for."""

# --- AI Helper Functions ---
async def analyze_screenshot_with_claude(image_base64):
    """Send screenshot to Claude API for analysis"""
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set")
        return None
    
    # Use hostname for HTTPS requests to avoid SSL issues
    api_url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 2000,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Please analyze this screenshot and provide the answer to any question or problem you see. Follow the format: answer=[your answer]"
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_base64
                        }
                    }
                ]
            }
        ]
    }
    
    try:
        # Create a longer timeout and better error handling
        timeout = aiohttp.ClientTimeout(total=60, connect=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, headers=headers, json=payload) as response:
                if response.status == 200:
                    result = await response.json()
                    content = result.get('content', [])
                    if content and len(content) > 0:
                        return content[0].get('text', '')
                else:
                    error_text = await response.text()
                    logger.error(f"Claude API error {response.status}: {error_text}")
                    return None
    except asyncio.TimeoutError:
        logger.error("Claude API request timed out")
        return None
    except Exception as e:
        logger.error(f"Error calling Claude API: {e}")
        return None

def parse_ai_answer(ai_response):
    """Parse AI response to extract answer and determine if it's clickable or essay"""
    if not ai_response:
        return None, None, None
    
    # Look for answer= pattern
    if "answer=" in ai_response.lower():
        answer_start = ai_response.lower().find("answer=") + 7
        answer = ai_response[answer_start:].strip()
        
        # Determine if it's a clickable answer (single letter/number) or essay
        if len(answer) <= 5 and (answer.isalnum() or answer in ['A', 'B', 'C', 'D', 'E', 'True', 'False']):
            return answer, "clickable", ai_response
        else:
            return answer, "essay", ai_response
    
    return None, None, ai_response

# --- Redis Helper Functions with Error Handling ---
def safe_redis_get(key, default=None):
    try:
        return redis_client.get(key)
    except Exception as e:
        logger.error(f"Redis GET error for key {key}: {e}")
        return default

def safe_redis_set(key, value, ex=None):
    try:
        return redis_client.set(key, value, ex=ex)
    except Exception as e:
        logger.error(f"Redis SET error for key {key}: {e}")
        return False

def safe_redis_delete(key):
    try:
        return redis_client.delete(key)
    except Exception as e:
        logger.error(f"Redis DELETE error for key {key}: {e}")
        return False

def safe_redis_exists(key):
    try:
        return redis_client.exists(key)
    except Exception as e:
        logger.error(f"Redis EXISTS error for key {key}: {e}")
        return False

# --- Authentication ---
def check_auth(password):
    return password == ACCESS_PASSWORD

# --- HTML Templates ---
LOGIN_HTML = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Remote Control - Login</title><script src="https://cdn.tailwindcss.com"></script><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet"><style> body { font-family: 'Inter', sans-serif; } </style></head><body class="bg-gray-100 flex items-center justify-center h-screen"><div class="bg-white p-8 rounded-lg shadow-md w-full max-w-sm"><h1 class="text-2xl font-semibold text-center text-gray-700 mb-6">Remote Access Login</h1>{% if error %}<div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded relative mb-4" role="alert"><span class="block sm:inline">{{ error }}</span></div>{% endif %}<form method="POST" action="{{ url_for('index') }}"><div class="mb-4"><label for="password" class="block text-gray-700 text-sm font-medium mb-2">Password</label><input type="password" id="password" name="password" required class="w-full px-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent" placeholder="Enter access password"></div><button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2 px-4 rounded-md transition duration-200 ease-in-out">Login</button></form><div class="mt-4 text-center text-sm text-gray-600">Password: mypassword123</div></div></body></html>
"""

INTERFACE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI-Enhanced Remote Desktop</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; }
        #main-content { display: flex; flex-direction: column; height: calc(100vh - 3.5rem); }
        #screen-view-area { flex-grow: 1; background-color: #000; overflow: hidden; position: relative; transition: height 0.3s ease-in-out; }
        #text-input-area { height: 0; overflow: hidden; background-color: #f9fafb; padding:0; transition: height 0.3s ease-in-out; }
        body.text-input-mode #screen-view-area { height: 50%; } body.text-input-mode #text-input-area { height: 50%; padding: 1rem; }
        #screen-view-area img { max-width: 100%; max-height: 100%; height: auto; width: auto; display: block; cursor: crosshair; object-fit: contain; }
        .status-dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 5px; }
        .status-connected { background-color: #4ade80; } .status-disconnected { background-color: #f87171; } .status-connecting { background-color: #fbbf24; }
        .control-button { padding: 0.5rem 1rem; background-color: #2563eb; color: white; border: none; border-radius: 0.375rem; cursor: pointer; transition: background-color 0.2s; margin-right: 0.5rem; }
        .control-button:hover { background-color: #1d4ed8; } .control-button.active { background-color: #16a34a; } .control-button.active:hover { background-color: #15803d; }
        .ai-button { background-color: #7c3aed; } .ai-button:hover { background-color: #6d28d9; } .ai-button.active { background-color: #059669; }
        #injection-text { width: 100%; flex-grow: 1; padding: 0.75rem; border: 1px solid #d1d5db; border-radius: 0.375rem; font-family: monospace; resize: none; }
        input[type=range] { -webkit-appearance: none; background: transparent; cursor: pointer; }
        input[type=range]::-webkit-slider-runnable-track { height: 4px; background: #60a5fa; border-radius: 2px; }
        input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; margin-top: -6px; background-color: #1d4ed8; height: 16px; width: 16px; border-radius: 50%; }
        #file-progress-container { position: fixed; bottom: 1rem; left: 50%; transform: translateX(-50%); background-color: rgba(0,0,0,0.7); color: white; padding: 0.5rem 1rem; border-radius: 8px; z-index: 100; display: none; }
        #file-progress-bar { width: 200px; height: 10px; background-color: #555; border-radius: 5px; overflow: hidden; }
        #file-progress { width: 0%; height: 100%; background-color: #4ade80; transition: width 0.2s; }
        #ai-status { position: fixed; top: 4rem; right: 1rem; background-color: rgba(0,0,0,0.8); color: white; padding: 0.5rem 1rem; border-radius: 8px; z-index: 100; display: none; }
        .ai-thinking { background-color: #7c3aed; animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .instructions { background-color: #eff6ff; border: 1px solid #3b82f6; border-radius: 0.5rem; padding: 0.75rem; margin: 0.5rem; font-size: 0.875rem; }
    </style>
</head>
<body class="bg-gray-200 flex flex-col h-screen" tabindex="0">
    <header class="bg-gray-800 text-white p-3 flex justify-between items-center shadow-md flex-shrink-0 h-14">
        <h1 class="text-lg font-semibold">🤖 AI Remote Desktop</h1>
        <div class="flex items-center space-x-4">
            <div class="flex items-center space-x-2 text-xs">
                <span title="Image Quality">Q:</span> <input type="range" id="quality-slider" min="10" max="95" value="75" class="w-20">
                <span title="Frames Per Second">FPS:</span> <input type="range" id="fps-slider" min="1" max="30" value="5" class="w-20">
            </div>
            <button id="ai-toggle-button" class="control-button ai-button text-xs" title="Toggle AI Assistant (F4 to analyze screen)">🤖 AI Off</button>
            <button id="upload-file-button" class="control-button text-xs">Upload File</button>
            <input type="file" id="file-input" style="display: none;">
            <button id="toggle-text-mode-button" class="control-button text-xs">Text Input</button>
            <div id="connection-status" class="flex items-center text-xs">
                <span id="status-dot" class="status-dot status-connecting"></span>
                <span id="status-text">Connecting...</span>
                <span id="latency-text" class="ml-2 text-gray-400">(---ms)</span>
            </div>
            <a href="{{ url_for('logout') }}" class="bg-red-600 hover:bg-red-700 text-white text-xs font-medium py-1 px-2 rounded-md">Logout</a>
        </div>
    </header>
    
    <div class="instructions">
        📖 <strong>Instructions:</strong> 
        1️⃣ Click 🤖 AI button to enable AI assistant | 
        2️⃣ On client: Press <strong>F4</strong> to analyze screenshot | 
        3️⃣ AI auto-clicks answers or stores essays | 
        4️⃣ Press <strong>F2</strong> to type stored text
    </div>
    
    <main id="main-content" class="p-2">
        <div id="screen-view-area">
            <img id="screen-image" src="data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=" alt="Remote Screen">
        </div>
        <div id="text-input-area" class="flex flex-col">
            <textarea id="injection-text" placeholder="🤖 AI answers appear here automatically. Manual text also works. Press F2 on client to type this text."></textarea>
            <div class="mt-2 flex justify-end">
                <p id="injection-status" class="text-xs text-green-600 mr-auto self-center"></p>
                <button id="send-injection-text-button" class="control-button text-sm">Save Text</button>
            </div>
        </div>
    </main>
    <div id="file-progress-container">
        <span id="file-name-progress"></span>
        <div id="file-progress-bar"><div id="file-progress"></div></div>
        <span id="file-percent-progress">0%</span>
    </div>
    <div id="ai-status">🤖 AI is thinking...</div>
    <script>
    document.addEventListener('DOMContentLoaded', () => {
        const socket = io(window.location.origin, { path: '/socket.io/' });
        const screenImage = document.getElementById('screen-image');
        const statusDot = document.getElementById('status-dot');
        const statusText = document.getElementById('status-text');
        const latencyText = document.getElementById('latency-text');
        const toggleTextModeButton = document.getElementById('toggle-text-mode-button');
        const qualitySlider = document.getElementById('quality-slider');
        const fpsSlider = document.getElementById('fps-slider');
        const uploadButton = document.getElementById('upload-file-button');
        const fileInput = document.getElementById('file-input');
        const aiToggleButton = document.getElementById('ai-toggle-button');
        const aiStatus = document.getElementById('ai-status');
        const injectionText = document.getElementById('injection-text');
        
        let remoteScreenWidth = null;
        let remoteScreenHeight = null;
        let currentImageUrl = null;
        let aiEnabled = false;
        
        document.body.focus();
        document.addEventListener('click', (e) => { if (document.getElementById('text-input-area').contains(e.target)) return; document.body.focus(); });
        
        function updateStatus(status, message) { statusText.textContent = message; statusDot.className = `status-dot ${status}`; }
        function cleanupState() { remoteScreenWidth = null; remoteScreenHeight = null; screenImage.src = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs='; }
        
        function showAiStatus() {
            aiStatus.style.display = 'block';
            aiStatus.className = 'ai-thinking';
        }
        
        function hideAiStatus() {
            aiStatus.style.display = 'none';
            aiStatus.className = '';
        }
        
        // AI Toggle
        aiToggleButton.addEventListener('click', () => {
            aiEnabled = !aiEnabled;
            aiToggleButton.textContent = aiEnabled ? '🤖 AI On' : '🤖 AI Off';
            aiToggleButton.className = aiEnabled ? 'control-button ai-button text-xs active' : 'control-button ai-button text-xs';
            socket.emit('toggle_ai_mode', { enabled: aiEnabled });
        });
        
        socket.on('connect', () => { updateStatus('status-connecting', 'Server connected, waiting for PC...'); socket.emit('check_client_status'); });
        socket.on('disconnect', () => { updateStatus('status-disconnected', 'Server disconnected'); cleanupState(); });
        socket.on('connect_error', () => { updateStatus('status-disconnected', 'Connection Error'); cleanupState(); });
        socket.on('client_connected', () => { updateStatus('status-connected', 'Remote PC Connected'); document.body.focus(); });
        socket.on('client_disconnected', () => { updateStatus('status-disconnected', 'Remote PC Disconnected'); cleanupState(); });
        socket.on('command_error', (data) => console.error(`Command Error: ${data.message}`));
        
        socket.on('ai_analysis_started', () => {
            showAiStatus();
        });
        
        socket.on('ai_analysis_complete', (data) => {
            hideAiStatus();
            if (data.answer) {
                if (data.answer_type === 'essay') {
                    injectionText.value = data.answer;
                    document.body.classList.add('text-input-mode');
                    document.getElementById('injection-status').textContent = '🤖 AI essay answer loaded! Press F2 on client to type.';
                } else {
                    document.getElementById('injection-status').textContent = `🤖 AI found answer: ${data.answer} (will auto-click)`;
                }
                socket.emit('set_injection_text', { text_to_inject: injectionText.value });
            } else {
                document.getElementById('injection-status').textContent = '🤖 AI could not find an answer in the screenshot.';
            }
            setTimeout(() => { document.getElementById('injection-status').textContent = ''; }, 5000);
        });
        
        socket.on('ai_analysis_error', () => {
            hideAiStatus();
            document.getElementById('injection-status').textContent = '❌ AI analysis failed. Check API key.';
            setTimeout(() => { document.getElementById('injection-status').textContent = ''; }, 3000);
        });
        
        socket.on('screen_frame_bytes', (imageDataBytes) => {
            const blob = new Blob([imageDataBytes], { type: 'image/jpeg' });
            const newImageUrl = URL.createObjectURL(blob);
            if (remoteScreenWidth === null) {
                const tempImg = new Image();
                tempImg.onload = () => { remoteScreenWidth = tempImg.naturalWidth; remoteScreenHeight = tempImg.naturalHeight; URL.revokeObjectURL(tempImg.src); };
                tempImg.src = newImageUrl;
            }
            if (currentImageUrl) URL.revokeObjectURL(currentImageUrl);
            currentImageUrl = newImageUrl;
            screenImage.src = newImageUrl;
        });
        
        setInterval(() => { const start = Date.now(); socket.emit('ping_from_browser', () => { const latency = Date.now() - start; latencyText.textContent = `(${latency}ms)`; }); }, 2000);
        
        screenImage.addEventListener('mousemove', (event) => { if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'move', x: remoteX, y: remoteY }); });
        screenImage.addEventListener('click', (event) => { if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'click', button: 'left', x: remoteX, y: remoteY }); document.body.focus(); });
        screenImage.addEventListener('contextmenu', (event) => { event.preventDefault(); if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'click', button: 'right', x: remoteX, y: remoteY }); document.body.focus(); });
        screenImage.addEventListener('wheel', (event) => { event.preventDefault(); const dY = event.deltaY > 0 ? 1 : (event.deltaY < 0 ? -1 : 0); if (dY) socket.emit('control_command', { action: 'scroll', dy: dY }); });
        
        document.body.addEventListener('keydown', (event) => { if (document.activeElement.tagName === 'TEXTAREA') return; const keysToPrevent = ['Tab', 'Enter', 'Escape', 'Backspace', 'Delete', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' ']; if (keysToPrevent.includes(event.key) || (event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey)) { event.preventDefault(); } socket.emit('control_command', { action: 'keydown', key: event.key, code: event.code }); });
        document.body.addEventListener('keyup', (event) => { if (document.activeElement.tagName === 'TEXTAREA') return; socket.emit('control_command', { action: 'keyup', key: event.key, code: event.code }); });
        
        toggleTextModeButton.addEventListener('click', () => { document.body.classList.toggle('text-input-mode'); });
        document.getElementById('send-injection-text-button').addEventListener('click', () => { const text = document.getElementById('injection-text').value; socket.emit('set_injection_text', { text_to_inject: text }); });
        
        socket.on('text_injection_set_ack', (data) => { const statusEl = document.getElementById('injection-status'); statusEl.textContent = data.status === 'success' ? 'Text saved for client!' : `Error: ${data.message || 'Failed to save.'}`; setTimeout(() => { statusEl.textContent = ''; }, 3000); });
        
        function sendSettingsUpdate() { socket.emit('update_client_settings', { quality: parseInt(qualitySlider.value, 10), fps: parseInt(fpsSlider.value, 10) }); }
        qualitySlider.addEventListener('change', sendSettingsUpdate);
        fpsSlider.addEventListener('change', sendSettingsUpdate);
        
        socket.on('update_browser_clipboard', (data) => { if (navigator.clipboard && data.text) { navigator.clipboard.writeText(data.text).catch(err => console.error('Failed to write to browser clipboard:', err)); } });
        document.addEventListener('paste', (event) => { if (document.activeElement === document.body) { const text = event.clipboardData.getData('text'); if (text) socket.emit('clipboard_from_browser', { text: text }); } });
        
        uploadButton.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', (event) => { const file = event.target.files[0]; if (file) uploadFile(file); fileInput.value = ''; });
        
        function uploadFile(file) { const CHUNK_SIZE = 1024 * 1024; let offset = 0; const progressContainer = document.getElementById('file-progress-container'); const progressText = document.getElementById('file-name-progress'); const progressBar = document.getElementById('file-progress'); const progressPercent = document.getElementById('file-percent-progress'); progressText.textContent = `Uploading: ${file.name}`; progressContainer.style.display = 'block'; function readChunk() { const slice = file.slice(offset, offset + CHUNK_SIZE); const reader = new FileReader(); reader.onload = (e) => { if (e.target.error) { console.error('File read error:', e.target.error); progressContainer.style.display = 'none'; return; } socket.emit('file_chunk', { name: file.name, data: e.target.result, offset: offset }, (ack) => { if (ack.status !== 'ok') { console.error('File chunk upload failed:', ack.message); progressContainer.style.display = 'none'; return; } offset += e.target.result.byteLength; const percentComplete = Math.round((offset / file.size) * 100); progressBar.style.width = `${percentComplete}%`; progressPercent.textContent = `${percentComplete}%`; if (offset < file.size) { readChunk(); } else { socket.emit('file_upload_complete', { name: file.name, size: file.size }); setTimeout(() => { progressContainer.style.display = 'none'; }, 2000); } }); }; reader.readAsArrayBuffer(slice); } readChunk(); }
    });
    </script>
</body>
</html>
"""

# --- Health Check Background Task ---
def redis_health_check():
    while True:
        try:
            redis_client.ping()
            time.sleep(30)
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            time.sleep(5)

health_check_thread = threading.Thread(target=redis_health_check, daemon=True)
health_check_thread.start()

# --- Flask Routes ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        password = request.form.get('password')
        if check_auth(password):
            session['authenticated'] = True
            return redirect(url_for('interface'))
        return render_template_string(LOGIN_HTML, error="Invalid password")
    if session.get('authenticated'):
        return redirect(url_for('interface'))
    return render_template_string(LOGIN_HTML)

@app.route('/interface')
def interface():
    if not session.get('authenticated'):
        return redirect(url_for('index'))
    return render_template_string(INTERFACE_HTML)

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('index'))

# --- SocketIO Events ---
@socketio.on('connect')
def handle_connect():
    logger.info(f"Browser connected: SID={request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"A client disconnected: SID={request.sid}")

@socketio.on('disconnect', namespace='/')
def handle_client_disconnect():
    client_pc_sid = safe_redis_get(CLIENT_REDIS_KEY)
    if request.sid == client_pc_sid:
        logger.warning(f"Remote PC (SID: {client_pc_sid}) disconnected. Clearing Redis key.")
        safe_redis_delete(CLIENT_REDIS_KEY)
        emit('client_disconnected', broadcast=True, include_self=False)

@socketio.on('register_client')
def handle_register_client(data):
    client_token = data.get('token')
    sid = request.sid
    if client_token == ACCESS_PASSWORD:
        old_sid = safe_redis_get(CLIENT_REDIS_KEY)
        if old_sid and old_sid != sid:
            logger.warning(f"New client auth, disconnecting old client (SID: {old_sid})")
            try:
                server_disconnect_client(old_sid, silent=True)
            except Exception:
                pass
        
        if safe_redis_set(CLIENT_REDIS_KEY, sid):
            logger.info(f"Remote PC registered (SID: {sid}). State saved to Redis.")
            emit('client_connected', broadcast=True, include_self=False)
            emit('registration_success', room=sid)
        else:
            logger.error(f"Failed to save client SID to Redis for {sid}")
            emit('registration_fail', {'message': 'Server error.'}, room=sid)
            server_disconnect_client(sid)
    else:
        logger.warning(f"Client registration failed for SID {sid}. Incorrect password.")
        emit('registration_fail', {'message': 'Auth failed.'}, room=sid)
        server_disconnect_client(sid)

@socketio.on('check_client_status')
def check_client_status():
    try:
        if safe_redis_exists(CLIENT_REDIS_KEY):
            emit('client_connected')
    except Exception as e:
        logger.error(f"Error checking client status: {e}")

@socketio.on('ping_from_browser')
def handle_ping():
    pass

@socketio.on('screen_data_bytes')
def handle_screen_data_bytes(data):
    current_client_sid = safe_redis_get(CLIENT_REDIS_KEY)
    if current_client_sid == request.sid:
        emit('screen_frame_bytes', data, broadcast=True, include_self=False)

# --- AI-Related Events ---
@socketio.on('toggle_ai_mode')
def handle_toggle_ai_mode(data):
    if not session.get('authenticated'):
        return
    ai_enabled = data.get('enabled', False)
    safe_redis_set(AI_ENABLED_KEY, str(ai_enabled))
    forward_to_client('ai_mode_changed', {'enabled': ai_enabled})
    logger.info(f"AI mode {'enabled' if ai_enabled else 'disabled'}")

@socketio.on('ai_screenshot_request')
def handle_ai_screenshot_request(data):
    """Handle screenshot from client for AI analysis"""
    current_client_sid = safe_redis_get(CLIENT_REDIS_KEY)
    if current_client_sid != request.sid:
        return
    
    # Notify browsers that AI analysis is starting
    emit('ai_analysis_started', broadcast=True, include_self=False)
    
    screenshot_data = data.get('screenshot')
    if not screenshot_data:
        emit('ai_analysis_error', broadcast=True, include_self=False)
        return
    
    # Process screenshot with AI in background
    eventlet.spawn(process_ai_screenshot, screenshot_data)

def process_ai_screenshot(screenshot_base64):
    """Process screenshot with Claude AI"""
    try:
        # Run the async AI analysis
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        ai_response = loop.run_until_complete(analyze_screenshot_with_claude(screenshot_base64))
        loop.close()
        
        if ai_response:
            answer, answer_type, full_response = parse_ai_answer(ai_response)
            
            if answer:
                # Store answer in Redis for client access
                safe_redis_set(AI_ANSWER_KEY, json.dumps({
                    'answer': answer,
                    'type': answer_type,
                    'full_response': full_response
                }), ex=300)  # Expire after 5 minutes
                
                # Send result to all browsers - emit to all connected clients
                try:
                    socketio.emit('ai_analysis_complete', {
                        'answer': answer,
                        'answer_type': answer_type,
                        'full_response': full_response
                    })
                except Exception as e:
                    logger.error(f"Error emitting ai_analysis_complete: {e}")
                
                # Send to client for auto-action
                forward_to_client('ai_answer_ready', {
                    'answer': answer,
                    'type': answer_type
                })
                
                logger.info(f"AI Analysis complete: {answer} (type: {answer_type})")
            else:
                try:
                    socketio.emit('ai_analysis_complete', {'answer': None})
                except Exception as e:
                    logger.error(f"Error emitting ai_analysis_complete (no answer): {e}")
                logger.info("AI could not extract answer from screenshot")
        else:
            try:
                socketio.emit('ai_analysis_error', {'message': 'AI analysis failed'})
            except Exception as e:
                logger.error(f"Error emitting ai_analysis_error: {e}")
            logger.error("AI analysis failed")
            
    except Exception as e:
        logger.error(f"Error in AI screenshot processing: {e}")
        try:
            socketio.emit('ai_analysis_error', {'message': str(e)})
        except Exception as emit_error:
            logger.error(f"Error emitting error message: {emit_error}")

def forward_to_client(event, data):
    client_pc_sid = safe_redis_get(CLIENT_REDIS_KEY)
    if client_pc_sid:
        try:
            socketio.emit(event, data, room=client_pc_sid)
            return True
        except Exception as e:
            logger.error(f"Error forwarding {event} to client {client_pc_sid}: {e}")
            return False
    return False

@socketio.on('control_command')
def handle_control_command(data):
    if not session.get('authenticated'): 
        return
    if not forward_to_client('command', data):
        emit('command_error', {'message': 'Remote PC not connected.'})

@socketio.on('set_injection_text')
def handle_set_injection_text(data):
    if not session.get('authenticated'): 
        return
    if forward_to_client('receive_injection_text', {'text': data.get('text_to_inject', '')}):
        emit('text_injection_set_ack', {'status': 'success'})
    else:
        emit('text_injection_set_ack', {'status': 'error', 'message': 'Remote PC not connected.'})

@socketio.on('update_client_settings')
def handle_update_settings(data):
    if not session.get('authenticated'): 
        return
    logger.info(f"Forwarding settings update to client: {data}")
    forward_to_client('receive_settings_update', data)

@socketio.on('clipboard_from_browser')
def handle_clipboard_from_browser(data):
    if not session.get('authenticated'): 
        return
    forward_to_client('set_clipboard', data)

@socketio.on('clipboard_from_client')
def handle_clipboard_from_client(data):
    current_client_sid = safe_redis_get(CLIENT_REDIS_KEY)
    if current_client_sid == request.sid:
        emit('update_browser_clipboard', data, broadcast=True, include_self=False)

@socketio.on('file_chunk')
def handle_file_chunk(data, callback):
    if not session.get('authenticated'): 
        return
    if forward_to_client('receive_file_chunk', data):
        if callback: 
            callback({'status': 'ok'})
    else:
        if callback: 
            callback({'status': 'error', 'message': 'Client not connected'})

@socketio.on('file_upload_complete')
def handle_file_upload_complete(data):
    if not session.get('authenticated'): 
        return
    forward_to_client('file_transfer_complete', data)

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled exception: {e}")
    return "Internal Server Error", 500

if __name__ == '__main__':
    logger.info("--- 🤖 AI-Enhanced Remote Server Starting ---")
    port = int(os.environ.get('PORT', 5000))
    host = '0.0.0.0'
    logger.info(f"Listening on http://{host}:{port}")
    logger.info(f"Using Redis at {redis_host}:{redis_port} (resolved to {redis_ip})")
    logger.info(f"🔐 Login password: {ACCESS_PASSWORD}")
    
    if ANTHROPIC_API_KEY:
        logger.info("✅ Claude AI integration enabled")
    else:
        logger.warning("⚠️  ANTHROPIC_API_KEY not set - AI features will be disabled")
    
    socketio.run(app, host=host, port=port, debug=False)
else:
    # When running with gunicorn
    logger.info("--- 🤖 AI-Enhanced Remote Server Starting with Gunicorn ---")
    logger.info(f"🔐 Login password: {ACCESS_PASSWORD}")
    
    if ANTHROPIC_API_KEY:
        logger.info("✅ Claude AI integration enabled")
    else:
        logger.warning("⚠️  ANTHROPIC_API_KEY not set - AI features will be disabled")

# Export for gunicorn (SocketIO apps need the socketio object)
application = socketio
