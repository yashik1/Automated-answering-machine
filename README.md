# 🍕 Mr. Singh Pizza Automated Answering Machine

An intelligent, AI-powered automated answering system designed specifically for Mr. Singh Pizza restaurant. This system handles customer calls 24/7, takes orders, answers questions, manages reservations, and provides information about the menu and services.

## Features

- **24/7 Automated Phone Service** - Never miss a customer call
- **Natural Language Processing** - Understands customer requests in natural speech
- **Order Management** - Take, modify, and track pizza orders
- **Menu Information** - Provide detailed menu information and recommendations
- **Reservation System** - Handle table bookings and inquiries
- **Customer Information** - Look up customer history and preferences
- **Order Status Updates** - Provide real-time updates on order preparation
- **FAQ Handling** - Answer common questions about hours, location, policies
- **Loyalty Program Integration** - Track and update customer rewards
- **Multi-language Support** - Configureable for different languages
- **Analytics & Reporting** - Track call volume, popular items, peak times

## System Architecture

```
Mr. Singh Pizza Automated Answering Machine
├── src/
│   ├── main.py              # PizzaBot core: menu, orders, customers, SQLite; CLI demo
│   ├── conversation.py      # Phone conversation state machine (order flow)
│   └── app.py               # Flask app: Twilio voice webhooks + website + JSON API
├── config.py                # Configuration constants
├── templates/
│   └── index.html           # Informational website
├── tests/
│   ├── test_pizza_bot.py    # Unit tests for the bot core
│   └── test_voice.py        # End-to-end tests of the Twilio voice flow
├── data/                    # SQLite database (created at runtime, gitignored)
├── logs/                    # Application logs (created at runtime, gitignored)
├── requirements.txt         # Python dependencies
├── config.example.env       # Example environment configuration
├── Procfile                 # Production start command (gunicorn)
├── DEPLOYMENT.md            # Testing + deployment walkthrough
└── README.md                # This file
```

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/Automated-answering-machine.git
   cd Automated-answering-machine
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**
   ```bash
   cp config.example.env .env
   # Edit .env with your actual configuration values
   ```

5. **Run it**

   Web + phone server (answers Twilio calls, serves the website/API):
   ```bash
   python src/app.py            # http://localhost:5000
   ```

   Or the interactive terminal demo (no phone/server needed):
   ```bash
   python src/main.py
   ```

   The SQLite database, `logs/`, and `data/` directories are created
   automatically on first run, seeded with the default menu and sample
   customers — no separate initialization step is needed.

> **To answer real phone calls**, see [DEPLOYMENT.md](DEPLOYMENT.md) for the
> full walkthrough: local testing with `curl`, testing with a real Twilio
> number via an ngrok tunnel, and running in production on the client's
> machine with gunicorn + systemd.

## Configuration

Copy `config.example.env` to `.env` and modify the following:

### Required Settings
- `TWILIO_ACCOUNT_SID` - Your Twilio account SID
- `TWILIO_AUTH_TOKEN` - Your Twilio auth token  
- `TWILIO_PHONE_NUMBER` - Your Twilio phone number
- `SECRET_KEY` - A secret key for Flask sessions

### Optional Settings
- Adjust business hours, delivery fees, loyalty program settings
- Configure TTS (Text-to-Speech) and STT (Speech-to-Text) engines
- Set logging levels and file locations

## Usage

### As a Web Application
Visit `http://localhost:5000` to see the web interface showing:
- Restaurant information
- Current specials
- Menu items
- System status

### As a Phone System (Twilio)
The phone system is built on Twilio Programmable Voice. Twilio provides the
number, transcribes the caller's speech, and speaks the bot's replies; our
server answers Twilio's webhooks with TwiML.

1. Start the server: `python src/app.py` (or gunicorn in production).
2. Expose it over public HTTPS (ngrok for testing, a cloud host or reverse
   proxy in production).
3. In the Twilio console, set your number's Voice webhook ("A call comes
   in") to `https://<public-host>/voice` with method **HTTP POST**.
4. Set `TWILIO_AUTH_TOKEN` in the environment so the server only accepts
   genuine Twilio requests.
5. Call the number and speak naturally to order, check an order, hear the
   menu, or ask about hours.

Webhook endpoints:
- `POST /voice` — answers the incoming call with a spoken greeting.
- `POST /voice/collect` — handles each caller utterance and drives the order
  conversation.

Full step-by-step instructions are in [DEPLOYMENT.md](DEPLOYMENT.md).

### Development Mode
For testing without a phone system:
```bash
python src/main.py demo
```
This runs a demonstration mode where you can interact with the system via text input.

## API Endpoints

The system provides a RESTful API for integration:

- `GET /` - Main information page
- `GET /menu` - Get full menu
- `GET /menu/<category>` - Get menu items by category
- `GET /orders/<order_id>` - Get order status
- `POST /orders` - Create a new order
- `POST /calls/log` - Log a call (used by telephony integration)
- `GET /stats` - Get system statistics

## Technology Stack

- **Backend**: Python 3.8+, Flask
- **Database**: SQLite (easily upgradable to PostgreSQL/MySQL)
- **Telephony**: Twilio API (configurable for other providers)
- **Speech**: Google Speech-to-Text, gTTS/pyttsx3 for TTS
- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **Deployment**: Can be deployed to any Python-supporting platform (Heroku, AWS, Docker, etc.)

## Customization

### Menu Items
Edit the `load_menu()` method in `src/main.py` or directly modify the menu_items table in the database to add/remove/update menu items.

### Business Logic
Modify the `PizzaBot` class in `src/main.py` to adjust:
- Order processing logic
- Customer interaction flows
- Business rules (discounts, minimum orders, etc.)

### Language Support
To add support for additional languages:
1. Configure the STT and TTS engines for the desired language
2. Update the response templates in the `process_call` method
3. Add language-specific menu descriptions if needed

## Testing

Run the pytest suite (unit tests for intent detection, order parsing,
totals, loyalty points, and order-status lookup):
```bash
pip install -r requirements.txt   # installs pytest
pytest
```

Or run the standalone smoke test, which checks that the app imports,
initializes its database, and loads the menu:
```bash
python test_installation.py
```

## Deployment

### Docker
```bash
docker build -t mr-singh-pizza-bot .
docker run -p 5000:5000 --env-file .env mr-singh-pizza-bot
```

### Cloud Platforms
The application is designed to be deployed on:
- Heroku
- AWS Elastic Beanstalk
- Google Cloud Platform
- Azure App Service
- Any VPS with Python support

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

For support, please open an issue in the GitHub repository or contact:
- Email: support@mrssinghpizza.com
- Phone: +1234567890 (during business hours)

---

**Made with ❤️ for Mr. Singh Pizza**  
*Bringing the flavors of India to every slice of pizza!*