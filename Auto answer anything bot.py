# Fix for OpenMP library conflict in Anaconda environments
import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

# Suppress cryptography warnings
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import requests
import base64
import io
import time
import threading
import re

# --- Core Automation Libraries ---
import keyboard  # For detecting the F2 key press
import pyautogui # For controlling the mouse and screenshots
import mss       # For taking fast screenshots of a specific monitor
from PIL import Image, ImageGrab # For handling image data

# Use EasyOCR instead of Tesseract (no complex installation needed!)
try:
    import easyocr
    OCR_AVAILABLE = True
    print("EasyOCR loaded successfully!")
except ImportError:
    OCR_AVAILABLE = False
    print("EasyOCR not found. Install with: pip install easyocr")

class QuizSolverApp:
    def __init__(self, root):
        self.root = root
        self.root.title("F2 Quiz Solver Control Panel - Claude Sonnet 4 + EasyOCR")
        self.root.geometry("650x500")
        self.root.attributes('-topmost', True) # Keep window on top

        self.api_key = ""
        self.monitor_number = 1 # Default monitor is 1 (primary)
        self.is_listening = False
        self.listener_thread = None
        
        # Initialize EasyOCR reader (supports 80+ languages)
        if OCR_AVAILABLE:
            self.ocr_reader = None  # Will be initialized when needed to save startup time
        
        # --- AI Configuration ---
        self.AI_MODEL = "claude-sonnet-4-20250514" # Latest Claude Sonnet 4 model
        self.SYSTEM_PROMPT = """You are an expert AI assistant analyzing quiz questions. Your task is to look at an image containing a multiple-choice question and identify the correct answer.

CRITICAL RESPONSE FORMAT:
You must respond in this EXACT format:
===
[CORRECT_ANSWER_TEXT]

Where [CORRECT_ANSWER_TEXT] is ONLY the text of the correct answer option.

Examples:
- If the correct answer is "Nitrogen", respond: ===\nNitrogen
- If the correct answer is "Paris", respond: ===\nParis  
- If the correct answer is "Option C", respond: ===\nOption C

Do NOT include:
- Explanations
- "The correct answer is:"
- Letter prefixes like "A)" unless that's the actual clickable text
- Any other text

Find the most complete and unique text string for the correct option that can be found on screen."""

        self.setup_ui()

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Configuration Section ---
        config_frame = ttk.LabelFrame(main_frame, text="Configuration", padding="10")
        config_frame.pack(fill=tk.X, pady=5)

        ttk.Label(config_frame, text="Anthropic API Key:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.api_key_var = tk.StringVar()
        # !!! IMPORTANT !!! PASTE YOUR API KEY HERE
        self.api_key_var.set("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")
        api_key_entry = ttk.Entry(config_frame, textvariable=self.api_key_var, width=70, show="*")
        api_key_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=5)

        ttk.Label(config_frame, text="Monitor Number:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.monitor_var = tk.IntVar(value=self.monitor_number)
        monitor_spinbox = ttk.Spinbox(config_frame, from_=0, to_=10, textvariable=self.monitor_var, width=5)
        monitor_spinbox.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        
        # Add info label for monitor selection
        info_label = ttk.Label(config_frame, text="Monitor: 0=All screens, 1=Primary, 2+=Secondary", font=("Arial", 8))
        info_label.grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)
        
        # OCR Status
        ocr_status = "✅ EasyOCR Ready" if OCR_AVAILABLE else "❌ EasyOCR Not Installed (pip install easyocr)"
        ocr_label = ttk.Label(config_frame, text=f"OCR Status: {ocr_status}", font=("Arial", 8))
        ocr_label.grid(row=3, column=1, sticky=tk.W, padx=5, pady=2)
        
        config_frame.columnconfigure(1, weight=1)

        # --- Control Section ---
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=10)
        self.start_button = ttk.Button(control_frame, text="🚀 Start Listening on F2", command=self.start_listening)
        self.start_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.stop_button = ttk.Button(control_frame, text="⏹️ Stop Listening", command=self.stop_listening, state=tk.DISABLED)
        self.stop_button.pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=5)

        # --- Status Log Section ---
        log_frame = ttk.LabelFrame(main_frame, text="Status Log", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def log(self, message):
        self.root.after(0, self._log_thread_safe, message)

    def _log_thread_safe(self, message):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        
    def start_listening(self):
        self.api_key = self.api_key_var.get()
        self.monitor_number = self.monitor_var.get()

        if not self.api_key or "YOUR_ANTHROPIC_API_KEY_HERE" in self.api_key:
            messagebox.showerror("Error", "Please enter your Anthropic API key.")
            return
            
        if not OCR_AVAILABLE:
            messagebox.showerror("Error", "EasyOCR is not installed. Please run: pip install easyocr")
            return

        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        
        # Initialize EasyOCR reader (this may take a moment on first run)
        if self.ocr_reader is None:
            self.log("Initializing EasyOCR (this may take a moment on first run)...")
            try:
                self.ocr_reader = easyocr.Reader(['en'], gpu=False)  # English only for speed
                self.log("EasyOCR initialized successfully!")
            except Exception as e:
                self.log(f"Failed to initialize EasyOCR: {e}")
                self.stop_listening()
                return
        
        # Run the keyboard listener in a separate thread to not freeze the GUI
        self.is_listening = True
        self.listener_thread = threading.Thread(target=self.keyboard_listener_thread, daemon=True)
        self.listener_thread.start()
        self.log("🎯 Started listening. Press F2 to solve a quiz on the screen.")
        self.log(f"🤖 Using Claude Sonnet 4 model: {self.AI_MODEL}")
        self.log(f"🖥️ Targeting Monitor: {self.monitor_number} (0=All screens, 1=Primary, 2+=Secondary)")

    def stop_listening(self):
        self.is_listening = False
        keyboard.unhook_all()
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.log("⏹️ Stopped listening for F2 key.")

    def keyboard_listener_thread(self):
        keyboard.on_press_key("f2", self.on_f2_pressed)
        while self.is_listening:
            time.sleep(0.1)
        keyboard.unhook_all()

    def on_f2_pressed(self, e):
        # To prevent running multiple times if key is held down
        if hasattr(self, 'processing') and self.processing:
            return
        
        self.processing = True
        # Run the main logic in a new thread to keep the listener responsive
        threading.Thread(target=self.solve_quiz_on_screen, daemon=True).start()

    def take_screenshot(self):
        """Take screenshot with multiple fallback methods to handle different Windows configurations"""
        screenshot = None
        monitor_info = None
        
        # Method 1: Try pyautogui (most reliable)
        try:
            self.log("📸 Attempting screenshot with pyautogui...")
            if self.monitor_number == 0:
                # Capture all screens
                screenshot = pyautogui.screenshot()
                monitor_info = {'left': 0, 'top': 0, 'width': screenshot.width, 'height': screenshot.height}
                self.log("✅ Screenshot taken of all screens using pyautogui")
            else:
                # For specific monitor, we'll use the primary screen with pyautogui
                # and adjust coordinates later if needed
                screenshot = pyautogui.screenshot()
                monitor_info = {'left': 0, 'top': 0, 'width': screenshot.width, 'height': screenshot.height}
                self.log(f"✅ Screenshot taken of primary screen using pyautogui")
            return screenshot, monitor_info
        except Exception as e:
            self.log(f"❌ pyautogui screenshot failed: {e}")
        
        # Method 2: Try PIL ImageGrab (Windows-specific)
        try:
            self.log("📸 Attempting screenshot with PIL ImageGrab...")
            screenshot = ImageGrab.grab()
            monitor_info = {'left': 0, 'top': 0, 'width': screenshot.width, 'height': screenshot.height}
            self.log("✅ Screenshot taken using PIL ImageGrab")
            return screenshot, monitor_info
        except Exception as e:
            self.log(f"❌ PIL ImageGrab screenshot failed: {e}")
        
        # Method 3: Try mss (original method)
        try:
            self.log("📸 Attempting screenshot with mss...")
            with mss.mss() as sct:
                monitors = sct.monitors
                self.log(f"🖥️ Found {len(monitors)-1} monitors with mss")
                
                if self.monitor_number == 0:
                    # Capture all screens
                    monitor_to_capture = monitors[0]  # monitors[0] is all screens combined
                elif self.monitor_number >= len(monitors):
                    self.log(f"⚠️ Monitor {self.monitor_number} not found. You only have {len(monitors)-1} monitors. Using primary monitor.")
                    monitor_to_capture = monitors[1]  # Primary monitor
                else:
                    monitor_to_capture = monitors[self.monitor_number]
                
                sct_img = sct.grab(monitor_to_capture)
                screenshot = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                monitor_info = monitor_to_capture
                self.log("✅ Screenshot taken using mss")
                return screenshot, monitor_info
        except Exception as e:
            self.log(f"❌ mss screenshot failed: {e}")
        
        # If all methods fail
        raise Exception("All screenshot methods failed. Please check your system configuration.")

    def parse_ai_response(self, response_text):
        """Parse AI response to extract answer after === markers"""
        try:
            # Look for === followed by the answer
            match = re.search(r'===\s*\n?\s*(.+)', response_text.strip(), re.MULTILINE | re.DOTALL)
            if match:
                answer = match.group(1).strip()
                # Remove any trailing text after newlines (keep only first line after ===)
                answer = answer.split('\n')[0].strip()
                return answer
            else:
                # Fallback: if no === found, return the whole response
                self.log("⚠️ No === markers found in AI response, using full response")
                return response_text.strip()
        except Exception as e:
            self.log(f"Error parsing AI response: {e}")
            return response_text.strip()

    def solve_quiz_on_screen(self):
        try:
            self.log("🚀 F2 pressed! Starting solution process...")
            
            # 1. Take Screenshot
            screenshot, monitor_info = self.take_screenshot()
            
            if not screenshot:
                self.log("❌ Error: Failed to capture screenshot")
                self.processing = False
                return

            # 2. Convert to Base64 and send to Claude
            self.log("🤖 Encoding image and asking Claude Sonnet 4 for the answer...")
            buffered = io.BytesIO()
            screenshot.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            ai_response = self.get_answer_from_claude(img_base64)
            if not ai_response or "Error:" in ai_response:
                self.log(f"❌ AI failed. Reason: {ai_response}")
                self.processing = False
                return
            
            # Parse the AI response to extract the answer after ===
            answer_text = self.parse_ai_response(ai_response)
            self.log(f"🎯 Claude Sonnet 4 identified the answer: '{answer_text}'")

            # 3. Find the location of the answer text on the screen using EasyOCR
            self.log("🔍 Searching for the answer text on the screen with EasyOCR...")
            location = self.find_text_location_easyocr(screenshot, answer_text)

            if not location:
                self.log(f"⚠️ Could not find the exact text '{answer_text}' on the screen. Trying partial matches...")
                # Fallback: try to find any part of the answer
                words = answer_text.split()
                for word in reversed(words):
                    if len(word) > 2: # Avoid matching very small words
                        location = self.find_text_location_easyocr(screenshot, word)
                        if location:
                            self.log(f"✅ Found partial match for '{word}' instead.")
                            break
            
            if not location:
                self.log("❌ Critical Error: Failed to locate the answer on screen even with partial matching.")
                self.log("💡 Tip: Make sure the quiz is clearly visible and the text is readable.")
                self.processing = False
                return

            # 4. Move the mouse to the found location
            # The location is relative to the screenshot, so we need to add the monitor's offset
            target_x = monitor_info['left'] + location['x']
            target_y = monitor_info['top'] + location['y']
            
            self.log(f"🎯 Answer found at coordinates ({target_x}, {target_y}). Moving mouse...")
            pyautogui.moveTo(target_x, target_y, duration=0.5, tween=pyautogui.easeInOutQuad)
            self.log("✅ Mouse moved successfully! Process complete.")

        except Exception as e:
            self.log(f"💥 An unexpected error occurred: {e}")
            import traceback
            self.log(f"🔧 Full error details: {traceback.format_exc()}")
        finally:
            self.processing = False

    def get_answer_from_claude(self, img_base64):
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01"
        }
        payload = {
            "model": self.AI_MODEL,
            "max_tokens": 200,
            "system": self.SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": img_base64
                            }
                        },
                        {
                            "type": "text",
                            "text": "What is the correct answer to this question? Use the === format as instructed."
                        }
                    ]
                }
            ]
        }
        try:
            self.log("📡 Sending request to Claude Sonnet 4...")
            response = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=30)
            response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
            result = response.json()
            answer = result['content'][0]['text'].strip()
            return answer
        except requests.exceptions.HTTPError as e:
            error_detail = ""
            try:
                error_detail = e.response.json()
            except:
                error_detail = e.response.text
            return f"Error: API request failed with status {e.response.status_code}: {error_detail}"
        except Exception as e:
            return f"Error: {str(e)}"

    def find_text_location_easyocr(self, image, text_to_find):
        """Use EasyOCR to find text location on screen"""
        try:
            if not self.ocr_reader:
                self.log("❌ EasyOCR reader not initialized")
                return None
                
            # Convert PIL image to numpy array for EasyOCR
            import numpy as np
            img_array = np.array(image)
            
            # Use EasyOCR to detect text
            results = self.ocr_reader.readtext(img_array)
            
            text_to_find_lower = text_to_find.lower()
            best_match = None
            best_confidence = 0
            
            self.log(f"🔍 EasyOCR found {len(results)} text regions")
            
            for (bbox, detected_text, confidence) in results:
                detected_text_lower = detected_text.strip().lower()
                
                # Check if our target text is in the detected text
                if text_to_find_lower in detected_text_lower:
                    # Calculate center of bounding box
                    # bbox is in format [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                    x_coords = [point[0] for point in bbox]
                    y_coords = [point[1] for point in bbox]
                    center_x = int(sum(x_coords) / len(x_coords))
                    center_y = int(sum(y_coords) / len(y_coords))
                    
                    if confidence > best_confidence:
                        best_match = {'x': center_x, 'y': center_y}
                        best_confidence = confidence
                        self.log(f"✅ Found text match: '{detected_text}' (confidence: {confidence:.2f})")
                    
                    # If we found an exact match with high confidence, use it immediately
                    if detected_text_lower == text_to_find_lower and confidence > 0.8:
                        return {'x': center_x, 'y': center_y}
            
            if best_match:
                self.log(f"🎯 Best match found with confidence: {best_confidence:.2f}")
            
            return best_match
            
        except Exception as e:
            self.log(f"❌ EasyOCR Error: {e}")
            return None

    def on_closing(self):
        self.stop_listening()
        self.root.destroy()


if __name__ == "__main__":
    # Configure pyautogui safety settings
    pyautogui.FAILSAFE = True  # Move mouse to top-left corner to abort
    pyautogui.PAUSE = 0.1      # Small pause between pyautogui calls
    
    root = tk.Tk()
    app = QuizSolverApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()