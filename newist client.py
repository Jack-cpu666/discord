import socketio
import time
import os
import io
import threading
import logging
import platform
import sys
import mss
import keyboard
import numpy as np
import pyperclip
import base64
import re
from PIL import Image, ImageChops

if platform.system() == "Windows":
    import ctypes
    import ctypes.wintypes as wintypes
    INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
    KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
    MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_WHEEL = 0x0001, 0x8000, 0x0002, 0x0004, 0x0008, 0x0010, 0x0800
    WHEEL_DELTA, SM_CXSCREEN, SM_CYSCREEN = 120, 0, 1
    ULONG_PTR = ctypes.POINTER(wintypes.ULONG)
    class MOUSEINPUT(ctypes.Structure): _fields_ = (("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR))
    class KEYBDINPUT(ctypes.Structure): _fields_ = (("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR))
    class _INPUT_UNION(ctypes.Union): _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT))
    class INPUT(ctypes.Structure): _fields_ = (("type", wintypes.DWORD), ("union", _INPUT_UNION))
    SendInput, GetSystemMetrics = ctypes.windll.user32.SendInput, ctypes.windll.user32.GetSystemMetrics
    def _create_input(input_type, input_union): inp = INPUT(); inp.type, inp.union = wintypes.DWORD(input_type), input_union; return inp
    def _send_inputs(inputs): return SendInput(len(inputs), (INPUT * len(inputs))(*inputs), ctypes.sizeof(INPUT))
    def press_key_ctypes(vk_code): _send_inputs([_create_input(INPUT_KEYBOARD, _INPUT_UNION(ki=KEYBDINPUT(wVk=vk_code)))])
    def release_key_ctypes(vk_code): _send_inputs([_create_input(INPUT_KEYBOARD, _INPUT_UNION(ki=KEYBDINPUT(wVk=vk_code, dwFlags=KEYEVENTF_KEYUP)))])
    def move_mouse_ctypes(x, y): _send_inputs([_create_input(INPUT_MOUSE, _INPUT_UNION(mi=MOUSEINPUT(dx=int(x*65535/GetSystemMetrics(SM_CXSCREEN)), dy=int(y*65535/GetSystemMetrics(SM_CYSCREEN)), dwFlags=MOUSEEVENTF_MOVE|MOUSEEVENTF_ABSOLUTE)))])
    def click_mouse_ctypes(button='left'): down, up = (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP) if button=='left' else (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP); _send_inputs([_create_input(INPUT_MOUSE, _INPUT_UNION(mi=MOUSEINPUT(dwFlags=d))) for d in [down, up]])
    def scroll_mouse_ctypes(amount): _send_inputs([_create_input(INPUT_MOUSE, _INPUT_UNION(mi=MOUSEINPUT(mouseData=int(amount * -WHEEL_DELTA), dwFlags=MOUSEEVENTF_WHEEL)))])
    CTYPES_VK_MAP = {'backspace': 8, 'tab': 9, 'enter': 13, 'shift': 16, 'ctrl': 17, 'alt': 18, 'capslock': 20, 'esc': 27, 'space': 32, 'pageup': 33, 'pagedown': 34, 'end': 35, 'home': 36, 'left': 37, 'up': 38, 'right': 39, 'down': 40, 'insert': 45, 'delete': 46, '0': 48, '1': 49, '2': 50, '3': 51, '4': 52, '5': 53, '6': 54, '7': 55, '8': 56, '9': 57, 'a': 65, 'b': 66, 'c': 67, 'd': 68, 'e': 69, 'f': 70, 'g': 71, 'h': 72, 'i': 73, 'j': 74, 'k': 75, 'l': 76, 'm': 77, 'n': 78, 'o': 79, 'p': 80, 'q': 81, 'r': 82, 's': 83, 't': 84, 'u': 85, 'v': 86, 'w': 87, 'x': 88, 'y': 89, 'z': 90, 'win': 91, 'f1': 112, 'f2': 113, 'f3': 114, 'f4': 115, 'f5': 116, 'f6': 117, 'f7': 118, 'f8': 119, 'f9': 120, 'f10': 121, 'f11': 122, 'f12': 123}
else:
    logging.warning("Non-Windows OS detected. Mouse/Keyboard control will be unavailable.")
    def press_key_ctypes(vk_code): pass
    def release_key_ctypes(vk_code): pass
    def move_mouse_ctypes(x, y): pass
    def click_mouse_ctypes(button='left'): pass
    def scroll_mouse_ctypes(amount): pass
    CTYPES_VK_MAP = {}

log_format = '%(asctime)s - %(threadName)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- HARDCODED Configuration ---
SERVER_URL = 'https://gggggggggggggggggggggggggggggggggggggggg-htmy.onrender.com'  # CHANGE THIS TO YOUR RENDER URL
ACCESS_PASSWORD = 'mypassword123'  # Must match server password
CAPTURE_MONITOR_INDEX = 3  # Monitor to capture (3 = primary)
CLIENT_TARGET_FPS = 5      # Screenshots per second
JPEG_QUALITY = 75          # Screenshot quality (10-95)
FRAME_DIFFERENCE_THRESHOLD = 0  # Sensitivity for detecting screen changes

sio = socketio.Client(reconnection_attempts=100, reconnection_delay=5, logger=False, engineio_logger=False)
is_registered = False
all_threads_stop_event = threading.Event()
selected_monitor_details = None
text_to_inject_globally = ""
current_file_handle = None
downloads_path = os.path.join(os.path.expanduser('~'), 'Downloads')

# AI-related variables
ai_enabled = False
ai_answer_pending = None
ai_answer_type = None

if not os.path.exists(downloads_path):
    os.makedirs(downloads_path)

KEY_MAP_JS_TO_CTYPES = {"Control": "ctrl", "Shift": "shift", "Alt": "alt", "Meta": "win", "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right", "Enter": "enter", "Escape": "esc", "Backspace": "backspace", "Delete": "delete", "Tab": "tab", " ": "space", "F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4", "F5": "f5", "F6": "f6", "F7": "f7", "F8": "f8", "F9": "f9", "F10": "f10", "F11": "f11", "F12": "f12"}

def map_key_name(js_key):
    return KEY_MAP_JS_TO_CTYPES.get(js_key, js_key.lower())

def capture_screenshot_for_ai():
    """Capture screenshot and send to server for AI analysis"""
    try:
        with mss.mss() as sct:
            if selected_monitor_details:
                sct_img = sct.grab(selected_monitor_details)
                screenshot = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
                
                # Convert to base64
                buffer = io.BytesIO()
                screenshot.save(buffer, format="JPEG", quality=85)
                screenshot_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                
                # Send to server for AI analysis
                sio.emit('ai_screenshot_request', {'screenshot': screenshot_b64})
                logger.info("🤖 Screenshot sent to AI for analysis")
                return True
    except Exception as e:
        logger.error(f"Error capturing screenshot for AI: {e}")
        return False

def find_text_on_screen(target_text):
    """Find text on screen and return approximate coordinates"""
    if not selected_monitor_details:
        return None
    
    screen_width = selected_monitor_details['width']
    screen_height = selected_monitor_details['height']
    
    # Common locations for different answer types
    if target_text.upper() in ['A', 'B', 'C', 'D', 'E']:
        # Multiple choice - typically on left side with vertical spacing
        letter_positions = {
            'A': (screen_width * 0.15, screen_height * 0.35),
            'B': (screen_width * 0.15, screen_height * 0.45),
            'C': (screen_width * 0.15, screen_height * 0.55),
            'D': (screen_width * 0.15, screen_height * 0.65),
            'E': (screen_width * 0.15, screen_height * 0.75)
        }
        return letter_positions.get(target_text.upper())
    
    elif target_text.upper() in ['TRUE', 'FALSE']:
        # True/False questions - typically side by side
        if target_text.upper() == 'TRUE':
            return (screen_width * 0.35, screen_height * 0.5)
        else:
            return (screen_width * 0.65, screen_height * 0.5)
    
    elif target_text.upper() in ['YES', 'NO']:
        # Yes/No questions
        if target_text.upper() == 'YES':
            return (screen_width * 0.35, screen_height * 0.5)
        else:
            return (screen_width * 0.65, screen_height * 0.5)
    
    elif target_text.isdigit() or '.' in target_text:
        # Numeric answers - try input fields in center
        return (screen_width * 0.5, screen_height * 0.5)
    
    # For other short answers, try common clickable areas
    common_click_areas = [
        (screen_width * 0.2, screen_height * 0.4),   # Left upper
        (screen_width * 0.2, screen_height * 0.5),   # Left center
        (screen_width * 0.2, screen_height * 0.6),   # Left lower
        (screen_width * 0.5, screen_height * 0.4),   # Center upper
        (screen_width * 0.5, screen_height * 0.5),   # Center
        (screen_width * 0.5, screen_height * 0.6),   # Center lower
        (screen_width * 0.8, screen_height * 0.4),   # Right upper
        (screen_width * 0.8, screen_height * 0.5),   # Right center
        (screen_width * 0.8, screen_height * 0.6),   # Right lower
    ]
    
    # Return first area for now - in a real implementation you'd use OCR
    return common_click_areas[0]

def auto_click_answer(answer):
    """Automatically click on the detected answer"""
    if not answer or platform.system() != "Windows":
        return False
    
    coordinates = find_text_on_screen(answer)
    if coordinates:
        x, y = coordinates
        logger.info(f"🎯 Auto-clicking answer '{answer}' at coordinates ({int(x)}, {int(y)})")
        
        # Move mouse and click with small delays for reliability
        move_mouse_ctypes(int(x), int(y))
        time.sleep(0.3)  # Allow time for mouse movement
        click_mouse_ctypes('left')
        time.sleep(0.1)  # Small delay after click
        return True
    else:
        logger.warning(f"❌ Could not find coordinates for answer: {answer}")
        return False

def screen_capture_loop():
    global selected_monitor_details, CLIENT_TARGET_FPS, JPEG_QUALITY, FRAME_DIFFERENCE_THRESHOLD
    logger.info(f"📸 CAPTURE_THREAD: Starting. FPS:{CLIENT_TARGET_FPS}, Quality:{JPEG_QUALITY}, Monitor:{CAPTURE_MONITOR_INDEX}")
    last_frame = None
    with mss.mss() as sct:
        try:
            monitor_definition = sct.monitors[CAPTURE_MONITOR_INDEX]
            selected_monitor_details = monitor_definition
            logger.info(f"📸 CAPTURE_THREAD: Capturing Monitor {CAPTURE_MONITOR_INDEX}: {monitor_definition}")
        except IndexError:
            logger.error(f"❌ CAPTURE_THREAD: Monitor index {CAPTURE_MONITOR_INDEX} is invalid. Exiting thread.")
            return

        while not all_threads_stop_event.is_set():
            target_interval = 1.0 / CLIENT_TARGET_FPS
            capture_start_time = time.time()
            sct_img = sct.grab(monitor_definition)
            current_frame = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
            send_frame = False
            if last_frame is None:
                send_frame = True
            else:
                diff = ImageChops.difference(current_frame, last_frame)
                mean_diff = np.mean(np.array(diff))
                if mean_diff > FRAME_DIFFERENCE_THRESHOLD:
                    send_frame = True
            if send_frame:
                try:
                    buffer = io.BytesIO()
                    current_frame.save(buffer, format="JPEG", quality=JPEG_QUALITY)
                    sio.emit('screen_data_bytes', buffer.getvalue())
                    last_frame = current_frame
                except Exception as e:
                    logger.error(f"📸 CAPTURE_THREAD: Emit error: {e}")
                    time.sleep(1)
            elapsed = time.time() - capture_start_time
            sleep_duration = target_interval - elapsed
            if sleep_duration > 0:
                time.sleep(sleep_duration)
    logger.info("📸 CAPTURE_THREAD: Stopped.")

def clipboard_monitor_loop():
    logger.info("📋 CLIPBOARD_THREAD: Starting.")
    recent_value = ""
    try:
        recent_value = pyperclip.paste()
    except:
        pass
    while not all_threads_stop_event.is_set():
        try:
            current_value = pyperclip.paste()
            if current_value != recent_value:
                recent_value = current_value
                sio.emit('clipboard_from_client', {'text': current_value})
        except pyperclip.PyperclipException:
            pass
        except Exception as e:
            logger.error(f"📋 CLIPBOARD_THREAD: Unhandled error: {e}")
        time.sleep(1)
    logger.info("📋 CLIPBOARD_THREAD: Stopped.")

def local_key_listener_loop():
    global ai_enabled, ai_answer_pending, ai_answer_type
    logger.info("⌨️  KEY_LISTENER_THREAD: Listening for hotkeys")
    logger.info("⌨️  F2 = Type stored text | F4 = AI screenshot analysis")
    
    def on_f2():
        """F2 - Inject stored text (including AI answers)"""
        if text_to_inject_globally:
            logger.info(f"⌨️  F2 pressed: Typing {len(text_to_inject_globally)} characters")
            try:
                keyboard.write(text_to_inject_globally, delay=0.03)
                logger.info("✅ Text injection complete")
            except Exception as e:
                logger.error(f"❌ Failed to inject text: {e}")
        else:
            logger.info("⌨️  F2 pressed but no text stored")
    
    def on_f4():
        """F4 - Trigger AI screenshot analysis"""
        if ai_enabled:
            logger.info("🤖 F4 pressed: Capturing screenshot for AI analysis")
            success = capture_screenshot_for_ai()
            if success:
                logger.info("✅ Screenshot captured and sent to AI")
            else:
                logger.error("❌ Failed to capture screenshot")
        else:
            logger.info("🤖 F4 pressed but AI is disabled - enable AI in web interface first")
    
    try:
        keyboard.add_hotkey('f2', on_f2, suppress=False)
        keyboard.add_hotkey('f4', on_f4, suppress=False)
        logger.info("✅ Hotkeys registered successfully")
        all_threads_stop_event.wait()
    except Exception as e:
        logger.error(f"❌ KEY_LISTENER_THREAD: Error setting up hotkeys - {e}")
        logger.error("⚠️  This might require administrator privileges on Windows")
    finally:
        try:
            keyboard.remove_hotkey('f2')
            keyboard.remove_hotkey('f4')
        except:
            pass
        logger.info("⌨️  KEY_LISTENER_THREAD: Stopped.")

@sio.event
def connect():
    logger.info("🔗 Connected to server. Sending registration...")
    sio.emit('register_client', {'token': ACCESS_PASSWORD})

@sio.event
def disconnect():
    global is_registered
    logger.warning("❌ CLIENT: Disconnected from server. Stopping all tasks.")
    is_registered = False
    all_threads_stop_event.set()

@sio.on('registration_success')
def on_registration_success():
    global is_registered
    if is_registered:
        return
    is_registered = True
    all_threads_stop_event.clear()
    logger.info("✅ CLIENT: Successfully registered with server. Starting worker threads.")
    threading.Thread(target=screen_capture_loop, name="ScreenCaptureThread", daemon=True).start()
    threading.Thread(target=clipboard_monitor_loop, name="ClipboardThread", daemon=True).start()
    threading.Thread(target=local_key_listener_loop, name="KeyListenerThread", daemon=True).start()

@sio.on('registration_fail')
def on_registration_fail(data):
    logger.error(f"❌ CLIENT: Registration failed: {data.get('message')}. Shutting down.")
    logger.error(f"⚠️  Make sure password matches: '{ACCESS_PASSWORD}'")
    sio.disconnect()

@sio.on('command')
def on_command(data):
    if not is_registered or platform.system() != "Windows":
        return
    action = data.get('action')
    try:
        if action == 'move':
            x, y = data.get('x'), data.get('y')
            if x is not None and y is not None:
                move_mouse_ctypes(x, y)
        elif action == 'click':
            x, y = data.get('x'), data.get('y')
            if x is not None and y is not None:
                move_mouse_ctypes(x, y)
                time.sleep(0.01)
            click_mouse_ctypes(data.get('button', 'left'))
        elif action == 'scroll':
            scroll_mouse_ctypes(data.get('dy', 0))
        elif action == 'keydown':
            key = map_key_name(data['key'])
            if key in CTYPES_VK_MAP:
                press_key_ctypes(CTYPES_VK_MAP[key])
        elif action == 'keyup':
            key = map_key_name(data['key'])
            if key in CTYPES_VK_MAP:
                release_key_ctypes(CTYPES_VK_MAP[key])
    except Exception as e:
        logger.error(f"❌ CLIENT: Error processing command {data}: {e}")

@sio.on('receive_injection_text')
def on_receive_injection_text(data):
    global text_to_inject_globally
    text_to_inject_globally = data.get('text', "")
    logger.info(f"📝 Text for F2 injection updated ({len(text_to_inject_globally)} chars)")

@sio.on('receive_settings_update')
def on_settings_update(data):
    global CLIENT_TARGET_FPS, JPEG_QUALITY
    if 'fps' in data:
        CLIENT_TARGET_FPS = data['fps']
    if 'quality' in data:
        JPEG_QUALITY = data['quality']
    logger.info(f"⚙️  Settings updated: FPS={CLIENT_TARGET_FPS}, Quality={JPEG_QUALITY}")

@sio.on('set_clipboard')
def on_set_clipboard(data):
    try:
        pyperclip.copy(data.get('text', ''))
        logger.info("📋 Clipboard updated by server")
    except Exception as e:
        logger.error(f"❌ Failed to set clipboard: {e}")

@sio.on('receive_file_chunk')
def on_receive_file_chunk(data):
    global current_file_handle
    try:
        file_name = data['name']
        file_path = os.path.join(downloads_path, os.path.basename(file_name))
        if data['offset'] == 0:
            if current_file_handle:
                current_file_handle.close()
            current_file_handle = open(file_path, 'wb')
            logger.info(f"📥 Receiving file: {file_path}")
        if current_file_handle:
            current_file_handle.write(data['data'])
    except Exception as e:
        logger.error(f"❌ Error writing file chunk for {data.get('name')}: {e}")

@sio.on('file_transfer_complete')
def on_file_transfer_complete(data):
    global current_file_handle
    if current_file_handle:
        current_file_handle.close()
        current_file_handle = None
        logger.info(f"✅ File transfer complete: {data['name']} ({data['size']} bytes)")

# --- AI-Related Event Handlers ---
@sio.on('ai_mode_changed')
def on_ai_mode_changed(data):
    global ai_enabled
    ai_enabled = data.get('enabled', False)
    status = "enabled" if ai_enabled else "disabled"
    logger.info(f"🤖 AI mode {status} by server")
    if ai_enabled:
        logger.info("🤖 AI is now active - press F4 to analyze screenshots")
    else:
        logger.info("🤖 AI is now inactive")

@sio.on('ai_answer_ready')
def on_ai_answer_ready(data):
    """Handle AI answer from server"""
    global ai_answer_pending, ai_answer_type, text_to_inject_globally
    
    answer = data.get('answer')
    answer_type = data.get('type', 'clickable')
    
    if not answer:
        logger.info("🤖 AI analysis complete but no answer found")
        return
    
    logger.info(f"🤖 AI found answer: '{answer}' (type: {answer_type})")
    
    if answer_type == 'clickable':
        # Auto-click for multiple choice/short answers
        logger.info(f"🎯 Attempting to auto-click answer: {answer}")
        success = auto_click_answer(answer)
        if success:
            logger.info(f"✅ Successfully auto-clicked answer: {answer}")
        else:
            logger.warning(f"❌ Failed to auto-click, storing as text instead: {answer}")
            # Fallback - store as text for manual F2 injection
            text_to_inject_globally = answer
    
    elif answer_type == 'essay':
        # Store essay for F2 typing
        text_to_inject_globally = answer
        logger.info(f"📝 Essay answer stored for F2 typing ({len(answer)} characters)")
        logger.info("💡 Press F2 to type the essay answer")
    
    # Store for potential manual use
    ai_answer_pending = answer
    ai_answer_type = answer_type

def main():
    threading.current_thread().name = "MainThread"
    print("=" * 60)
    print("🤖 AI-Enhanced Remote Control Client")
    print("=" * 60)
    logger.info(f"🔗 Server URL: {SERVER_URL}")
    logger.info(f"🔐 Password: {ACCESS_PASSWORD}")
    logger.info(f"📸 Monitor: {CAPTURE_MONITOR_INDEX}")
    logger.info(f"⚙️  FPS: {CLIENT_TARGET_FPS}, Quality: {JPEG_QUALITY}")
    print("=" * 60)
    print("⌨️  HOTKEYS:")
    print("   F2 = Type stored text (manual or AI answers)")
    print("   F4 = Capture screenshot for AI analysis")
    print("=" * 60)
    print("🤖 AI WORKFLOW:")
    print("   1. Enable AI in web interface (🤖 button)")
    print("   2. Press F4 when you see a question")
    print("   3. AI automatically clicks answers or stores essays")
    print("   4. For essays, press F2 to type the answer")
    print("=" * 60)
    
    if ACCESS_PASSWORD == 'mypassword123':
        logger.warning("⚠️  USING DEFAULT PASSWORD - Change it in the code!")
    
    if 'your-app-name' in SERVER_URL:
        logger.warning("⚠️  UPDATE SERVER_URL in the code with your Render URL!")
    
    if platform.system() != "Windows":
        logger.warning("⚠️  Non-Windows OS - Mouse/Keyboard control unavailable")
    
    try:
        logger.info(f"🔗 Connecting to {SERVER_URL}...")
        sio.connect(SERVER_URL, transports=['websocket'], wait_timeout=20)
        logger.info("✅ Connected successfully!")
        sio.wait()
    except socketio.exceptions.ConnectionError as e:
        logger.critical(f"❌ FATAL: Connection failed: {e}")
        logger.critical("💡 Check your SERVER_URL and internet connection")
    except KeyboardInterrupt:
        logger.info("🛑 Shutdown requested by user (Ctrl+C)")
    except Exception as e:
        logger.critical(f"❌ FATAL: Unexpected error: {e}")
    finally:
        logger.info("🛑 Initiating shutdown...")
        all_threads_stop_event.set()
        if sio.connected:
            sio.disconnect()
        logger.info("✅ Client shutdown complete")

if __name__ == '__main__':
    main()