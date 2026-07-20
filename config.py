# Mr. Singh Pizza Automated Answering Machine
# Configuration file

import os
from pathlib import Path

# Base directory (this file lives at the project root)
BASE_DIR = Path(__file__).resolve().parent

# Database configuration
DATABASE_URL = f"sqlite:///{BASE_DIR}/data/pizza_bot.db"

# Telephony configuration (for Twilio integration)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "your_account_sid_here")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "your_auth_token_here")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "+1234567890")

# Server configuration
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 5000))
DEBUG = os.getenv("DEBUG", "False").lower() == "true"

# Speech synthesis configuration
TTS_ENGINE = os.getenv("TTS_ENGINE", "pyttsx3")  # Options: pyttsx3, gtts, azure, aws
TTS_VOICE_RATE = int(os.getenv("TTS_VOICE_RATE", 180))  # Words per minute
TTS_VOLUME = float(os.getenv("TTS_VOLUME", 0.9))  # 0.0 to 1.0

# Speech recognition configuration
STT_ENGINE = os.getenv("STT_ENGINE", "google")  # Options: google, sphinx, wit, azure, aws
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "en-US")
STT_TIMEOUT = int(os.getenv("STT_TIMEOUT", 5))  # Seconds
STT_PHRASE_TIME_LIMIT = int(os.getenv("STT_PHRASE_TIME_LIMIT", 10))  # Seconds

# Business configuration
RESTAURANT_NAME = "Mr. Singh Pizza"
RESTAURANT_PHONE = os.getenv("RESTAURANT_PHONE", "+1234567890")
RESTAURANT_ADDRESS = os.getenv("RESTAURANT_ADDRESS", "123 Pizza Street, Food City, FC 12345")
DELIVERY_RADIUS_MILES = float(os.getenv("DELIVERY_RADIUS_MILES", 5.0))
FREE_DELIVERY_MINIMUM = float(os.getenv("FREE_DELIVERY_MINIMUM", 25.0))
DELIVERY_FEE = float(os.getenv("DELIVERY_FEE", 3.0))

# Business hours (24-hour format)
BUSINESS_HOURS = {
    "monday": {"open": "11:00", "close": "22:00"},
    "tuesday": {"open": "11:00", "close": "22:00"},
    "wednesday": {"open": "11:00", "close": "22:00"},
    "thursday": {"open": "11:00", "close": "22:00"},
    "friday": {"open": "11:00", "close": "23:00"},
    "saturday": {"open": "11:00", "close": "23:00"},
    "sunday": {"open": "12:00", "close": "21:00"}
}

# Menu categories
MENU_CATEGORIES = [
    "Pizza",
    "Specialty Pizza",
    "Sides",
    "Salads",
    "Beverages",
    "Desserts"
]

# Loyalty program settings
LOYALTY_POINTS_PER_DOLLAR = int(os.getenv("LOYALTY_POINTS_PER_DOLLAR", 1))
LOYALTY_BONUS_THRESHOLD = int(os.getenv("LOYALTY_BONUS_THRESHOLD", 1000))  # Points for bonus
LOYALTY_BONUS_POINTS = int(os.getenv("LOYALTY_BONUS_POINTS", 50))  # Bonus points

# Security and privacy
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", 30))
MAX_CALL_DURATION_MINUTES = int(os.getenv("MAX_CALL_DURATION_MINUTES", 15))
ENABLE_CALL_RECORDING = os.getenv("ENABLE_CALL_RECORDING", "False").lower() == "true"

# Logging configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILE = BASE_DIR / "logs" / "pizza_bot.log"

# Ensure directories exist
os.makedirs(BASE_DIR / "logs", exist_ok=True)
os.makedirs(BASE_DIR / "data", exist_ok=True)
os.makedirs(BASE_DIR / "templates", exist_ok=True)
os.makedirs(BASE_DIR / "static", exist_ok=True)
os.makedirs(BASE_DIR / "static" / "audio", exist_ok=True)