import eventlet
eventlet.monkey_patch()

import os
import time
import redis
from flask import Flask, request, session, redirect, url_for, render_template_string, jsonify
from flask_socketio import SocketIO, emit
from flask_socketio import disconnect as server_disconnect_client
import sys
import logging

# --- Logging Setup ---
log_format = '%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- Configuration ---
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'change_this_strong_secret_key_12345_server_v4')
ACCESS_PASSWORD = os.environ.get('REMOTE_ACCESS_PASSWORD', '1')
REDIS_URL = os.environ.get('REDIS_URL')

# --- App & Redis Setup ---
if not REDIS_URL:
    logger.critical("FATAL: REDIS_URL environment variable not set! The server cannot run without it.")
    sys.exit(1)

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB upload limit
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", ping_timeout=90, ping_interval=30,
                    max_http_buffer_size=20 * 1024 * 1024, logger=False, engineio_logger=False)

# Connect to Redis. decode_responses=True makes it return strings instead of bytes.
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
logger.info("Successfully connected to Redis.")

# --- Redis Key Management ---
# Using a consistent key for our single remote client.
# For a multi-client system, this would be more dynamic.
CLIENT_REDIS_KEY = f"remote_client_sid:{ACCESS_PASSWORD}"


# --- Authentication ---
def check_auth(password):
    return password == ACCESS_PASSWORD

# --- HTML Templates ---
# NOTE: Login HTML is unchanged from your version, so it's collapsed for brevity.
LOGIN_HTML = """
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Remote Control - Login</title><script src="https://cdn.tailwindcss.com"></script><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet"><style> body { font-family: 'Inter', sans-serif; } </style></head><body class="bg-gray-100 flex items-center justify-center h-screen"><div class="bg-white p-8 rounded-lg shadow-md w-full max-w-sm"><h1 class="text-2xl font-semibold text-center text-gray-700 mb-6">Remote Access Login</h1>{% if error %}<div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded relative mb-4" role="alert"><span class="block sm:inline">{{ error }}</span></div>{% endif %}<form method="POST" action="{{ url_for('index') }}"><div class="mb-4"><label for="password" class="block text-gray-700 text-sm font-medium mb-2">Password</label><input type="password" id="password" name="password" required class="w-full px-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent" placeholder="Enter access password"></div><button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2 px-4 rounded-md transition duration-200 ease-in-out">Login</button></form></div></body></html>
"""

INTERFACE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remote Control Interface</title>
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
        #injection-text { width: 100%; flex-grow: 1; padding: 0.75rem; border: 1px solid #d1d5db; border-radius: 0.375rem; font-family: monospace; resize: none; }
        input[type=range] { -webkit-appearance: none; background: transparent; cursor: pointer; }
        input[type=range]::-webkit-slider-runnable-track { height: 4px; background: #60a5fa; border-radius: 2px; }
        input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; margin-top: -6px; background-color: #1d4ed8; height: 16px; width: 16px; border-radius: 50%; }
        #file-progress-container { position: fixed; bottom: 1rem; left: 50%; transform: translateX(-50%); background-color: rgba(0,0,0,0.7); color: white; padding: 0.5rem 1rem; border-radius: 8px; z-index: 100; display: none; }
        #file-progress-bar { width: 200px; height: 10px; background-color: #555; border-radius: 5px; overflow: hidden; }
        #file-progress { width: 0%; height: 100%; background-color: #4ade80; transition: width 0.2s; }
    </style>
</head>
<body class="bg-gray-200 flex flex-col h-screen" tabindex="0">

    <header class="bg-gray-800 text-white p-3 flex justify-between items-center shadow-md flex-shrink-0 h-14">
        <h1 class="text-lg font-semibold">Remote Desktop</h1>
        <div class="flex items-center space-x-4">
            <div class="flex items-center space-x-2 text-xs">
                <span title="Image Quality">Q:</span> <input type="range" id="quality-slider" min="10" max="95" value="75" class="w-20">
                <span title="Frames Per Second">FPS:</span> <input type="range" id="fps-slider" min="1" max="30" value="5" class="w-20">
            </div>
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

    <main id="main-content" class="p-2">
        <div id="screen-view-area">
            <img id="screen-image" src="data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=" alt="Remote Screen">
        </div>
        <div id="text-input-area" class="flex flex-col">
            <textarea id="injection-text" placeholder="Text entered here will be saved. Client types it on F2 press..."></textarea>
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

        let remoteScreenWidth = null;
        let remoteScreenHeight = null;
        let currentImageUrl = null;

        document.body.focus();
        document.addEventListener('click', (e) => {
            if (document.getElementById('text-input-area').contains(e.target)) return;
            document.body.focus();
        });

        function updateStatus(status, message) { statusText.textContent = message; statusDot.className = `status-dot ${status}`; }
        function cleanupState() { remoteScreenWidth = null; remoteScreenHeight = null; screenImage.src = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs='; }

        socket.on('connect', () => { updateStatus('status-connecting', 'Server connected, waiting for PC...'); socket.emit('check_client_status'); });
        socket.on('disconnect', () => { updateStatus('status-disconnected', 'Server disconnected'); cleanupState(); });
        socket.on('connect_error', () => { updateStatus('status-disconnected', 'Connection Error'); cleanupState(); });
        socket.on('client_connected', () => { updateStatus('status-connected', 'Remote PC Connected'); document.body.focus(); });
        socket.on('client_disconnected', () => { updateStatus('status-disconnected', 'Remote PC Disconnected'); cleanupState(); });
        socket.on('command_error', (data) => console.error(`Command Error: ${data.message}`));

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

        // --- Ping / Latency ---
        setInterval(() => {
            const start = Date.now();
            socket.emit('ping_from_browser', () => {
                const latency = Date.now() - start;
                latencyText.textContent = `(${latency}ms)`;
            });
        }, 2000);

        // --- Mouse & Keyboard Handlers (Unchanged, collapsed for brevity) ---
        screenImage.addEventListener('mousemove', (event) => { if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'move', x: remoteX, y: remoteY }); });
        screenImage.addEventListener('click', (event) => { if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'click', button: 'left', x: remoteX, y: remoteY }); document.body.focus(); });
        screenImage.addEventListener('contextmenu', (event) => { event.preventDefault(); if (!remoteScreenWidth) return; const rect = screenImage.getBoundingClientRect(); const x = event.clientX - rect.left; const y = event.clientY - rect.top; const remoteX = Math.round((x / rect.width) * remoteScreenWidth); const remoteY = Math.round((y / rect.height) * remoteScreenHeight); socket.emit('control_command', { action: 'click', button: 'right', x: remoteX, y: remoteY }); document.body.focus(); });
        screenImage.addEventListener('wheel', (event) => { event.preventDefault(); const dY = event.deltaY > 0 ? 1 : (event.deltaY < 0 ? -1 : 0); if (dY) socket.emit('control_command', { action: 'scroll', dy: dY }); });
        document.body.addEventListener('keydown', (event) => { if (document.activeElement.tagName === 'TEXTAREA') return; const keysToPrevent = ['Tab', 'Enter', 'Escape', 'Backspace', 'Delete', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' ']; if (keysToPrevent.includes(event.key) || (event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey)) { event.preventDefault(); } socket.emit('control_command', { action: 'keydown', key: event.key, code: event.code }); });
        document.body.addEventListener('keyup', (event) => { if (document.activeElement.tagName === 'TEXTAREA') return; socket.emit('control_command', { action: 'keyup', key: event.key, code: event.code }); });

        // --- UI & Feature Handlers ---
        toggleTextModeButton.addEventListener('click', () => { document.body.classList.toggle('text-input-mode'); });
        document.getElementById('send-injection-text-button').addEventListener('click', () => {
            const text = document.getElementById('injection-text').value;
            socket.emit('set_injection_text', { text_to_inject: text });
        });
        socket.on('text_injection_set_ack', (data) => {
            const statusEl = document.getElementById('injection-status');
            statusEl.textContent = data.status === 'success' ? 'Text saved for client!' : `Error: ${data.message || 'Failed to save.'}`;
            setTimeout(() => { statusEl.textContent = ''; }, 3000);
        });

        // --- Dynamic Settings ---
        function sendSettingsUpdate() {
            socket.emit('update_client_settings', {
                quality: parseInt(qualitySlider.value, 10),
                fps: parseInt(fpsSlider.value, 10)
            });
        }
        qualitySlider.addEventListener('change', sendSettingsUpdate);
        fpsSlider.addEventListener('change', sendSettingsUpdate);

        // --- Clipboard Sync ---
        socket.on('update_browser_clipboard', (data) => {
            if (navigator.clipboard && data.text) {
                navigator.clipboard.writeText(data.text).catch(err => console.error('Failed to write to browser clipboard:', err));
            }
        });
        document.addEventListener('paste', (event) => {
            if (document.activeElement === document.body) {
                const text = event.clipboardData.getData('text');
                if (text) socket.emit('clipboard_from_browser', { text: text });
            }
        });

        // --- File Upload ---
        uploadButton.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', (event) => {
            const file = event.target.files[0];
            if (file) uploadFile(file);
            fileInput.value = ''; // Reset for next selection
        });
        function uploadFile(file) {
            const CHUNK_SIZE = 1024 * 1024; // 1MB
            let offset = 0;
            const progressContainer = document.getElementById('file-progress-container');
            const progressText = document.getElementById('file-name-progress');
            const progressBar = document.getElementById('file-progress');
            const progressPercent = document.getElementById('file-percent-progress');

            progressText.textContent = `Uploading: ${file.name}`;
            progressContainer.style.display = 'block';

            function readChunk() {
                const slice = file.slice(offset, offset + CHUNK_SIZE);
                const reader = new FileReader();
                reader.onload = (e) => {
                    if (e.target.error) {
                        console.error('File read error:', e.target.error);
                        progressContainer.style.display = 'none';
                        return;
                    }
                    socket.emit('file_chunk', {
                        name: file.name,
                        data: e.target.result,
                        offset: offset
                    }, (ack) => {
                        if (ack.status !== 'ok') {
                            console.error('File chunk upload failed:', ack.message);
                            progressContainer.style.display = 'none';
                            return;
                        }

                        offset += e.target.result.byteLength;
                        const percentComplete = Math.round((offset / file.size) * 100);
                        progressBar.style.width = `${percentComplete}%`;
                        progressPercent.textContent = `${percentComplete}%`;

                        if (offset < file.size) {
                            readChunk();
                        } else {
                            socket.emit('file_upload_complete', { name: file.name, size: file.size });
                            setTimeout(() => { progressContainer.style.display = 'none'; }, 2000);
                        }
                    });
                };
                reader.readAsArrayBuffer(slice);
            }
            readChunk();
        }
    });
    </script>
</body>
</html>
"""

# --- Flask Routes ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        password = request.form.get('password')
        if check_auth(password):
            session['authenticated'] = True; return redirect(url_for('interface'))
        return render_template_string(LOGIN_HTML, error="Invalid password")
    if session.get('authenticated'): return redirect(url_for('interface'))
    return render_template_string(LOGIN_HTML)

@app.route('/interface')
def interface():
    if not session.get('authenticated'): return redirect(url_for('index'))
    return render_template_string(INTERFACE_HTML)

@app.route('/logout')
def logout():
    session.pop('authenticated', None); return redirect(url_for('index'))

# --- SocketIO Events ---
@socketio.on('connect')
def handle_connect():
    logger.info(f"Browser connected: SID={request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Browser disconnected: SID={request.sid}")
    # This disconnect could be a browser tab, not the remote client.
    # The remote client's disconnect is handled separately when its SID matches.

@socketio.on('register_client')
def handle_register_client(data):
    client_token = data.get('token')
    sid = request.sid
    if client_token == ACCESS_PASSWORD:
        old_sid = redis_client.get(CLIENT_REDIS_KEY)
        if old_sid and old_sid != sid:
            logger.warning(f"New client auth, disconnecting old client (SID: {old_sid})")
            try: server_disconnect_client(old_sid, silent=True)
            except Exception: pass # Ignore errors if old client is already gone
        
        redis_client.set(CLIENT_REDIS_KEY, sid)
        logger.info(f"Remote PC registered (SID: {sid}). State saved to Redis.")
        emit('client_connected', broadcast=True, include_self=False)
        emit('registration_success', room=sid)
    else:
        emit('registration_fail', {'message': 'Auth failed.'}, room=sid); server_disconnect_client(sid)

# Overridden disconnect to handle remote client state
@socketio.on('disconnect', namespace='/')
def handle_client_disconnect():
    client_pc_sid = redis_client.get(CLIENT_REDIS_KEY)
    if request.sid == client_pc_sid:
        logger.warning(f"Remote PC (SID: {client_pc_sid}) disconnected. Clearing Redis key.")
        redis_client.delete(CLIENT_REDIS_KEY)
        emit('client_disconnected', broadcast=True, include_self=False)

@socketio.on('check_client_status')
def check_client_status():
    if redis_client.exists(CLIENT_REDIS_KEY):
        emit('client_connected')

@socketio.on('ping_from_browser')
def handle_ping():
    # Just acknowledging the event is enough for the client to calculate latency
    pass

@socketio.on('screen_data_bytes')
def handle_screen_data_bytes(data):
    if redis_client.get(CLIENT_REDIS_KEY) == request.sid:
        emit('screen_frame_bytes', data, broadcast=True, include_self=False)

# This function forwards any command to the client PC
def forward_to_client(event, data):
    client_pc_sid = redis_client.get(CLIENT_REDIS_KEY)
    if client_pc_sid:
        socketio.emit(event, data, room=client_pc_sid)
        return True
    return False

# --- Feature-specific Event Handlers ---

@socketio.on('control_command')
def handle_control_command(data):
    if not session.get('authenticated'): return
    if not forward_to_client('command', data):
        emit('command_error', {'message': 'Remote PC not connected.'})

@socketio.on('set_injection_text')
def handle_set_injection_text(data):
    if not session.get('authenticated'): return
    if forward_to_client('receive_injection_text', {'text': data.get('text_to_inject', '')}):
        emit('text_injection_set_ack', {'status': 'success'})
    else:
        emit('text_injection_set_ack', {'status': 'error', 'message': 'Remote PC not connected.'})

@socketio.on('update_client_settings')
def handle_update_settings(data):
    if not session.get('authenticated'): return
    logger.info(f"Forwarding settings update to client: {data}")
    forward_to_client('receive_settings_update', data)

@socketio.on('clipboard_from_browser')
def handle_clipboard_from_browser(data):
    if not session.get('authenticated'): return
    forward_to_client('set_clipboard', data)

@socketio.on('clipboard_from_client')
def handle_clipboard_from_client(data):
    if redis_client.get(CLIENT_REDIS_KEY) == request.sid:
        emit('update_browser_clipboard', data, broadcast=True, include_self=False)

@socketio.on('file_chunk')
def handle_file_chunk(data, callback):
    if not session.get('authenticated'): return
    if forward_to_client('receive_file_chunk', data):
        if callback: callback({'status': 'ok'})
    else:
        if callback: callback({'status': 'error', 'message': 'Client not connected'})

@socketio.on('file_upload_complete')
def handle_file_upload_complete(data):
    if not session.get('authenticated'): return
    forward_to_client('file_transfer_complete', data)


if __name__ == '__main__':
    logger.info("--- Advanced Remote Server Starting ---")
    port = int(os.environ.get('PORT', 5000)); host = '0.0.0.0'
    logger.info(f"Listening on http://{host}:{port}")
    if ACCESS_PASSWORD == '1': logger.warning("USING DEFAULT SERVER ACCESS PASSWORD '1'!")
    socketio.run(app, host=host, port=port, debug=False)
