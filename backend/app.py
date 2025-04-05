from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv
import os
import json
import google.generativeai as genai
from datetime import datetime, timedelta
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
from datetime import date
import random

# Plaid v8 imports
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.configuration import Configuration
from plaid.api_client import ApiClient

# Load .env values
load_dotenv()

app = Flask(__name__, template_folder='templates')
CORS(app)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

GAMBLING_KEYWORDS = ['draftkings', 'fanduel', 'betmgm', 'sportsbook', 'caesars', 'poker', 'casino']


# In-memory "database"
users = {}

USERS_FILE = "users.json"

# Load users from file
def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    return {}

# Save users to file
def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

# Initialize users dict
users = load_users()


# Set up Plaid client
configuration = Configuration(
    host="https://sandbox.plaid.com",
    api_key={
        'clientId': os.getenv("PLAID_CLIENT_ID"),
        'secret': os.getenv("PLAID_SECRET"),
    }
)
api_client = ApiClient(configuration)
plaid_client = plaid_api.PlaidApi(api_client)

@app.route('/')
def home():
    return render_template('signup.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        if email in users:
            return "User already exists", 400

        users[email] = {'password': password}
        save_users(users)  # 🔄 Save to file
        return redirect(url_for('login'))

    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        user = users.get(email)
        if not user or user['password'] != password:
            return "Invalid credentials", 401

        return redirect(url_for('connect_bank', email=email))

    return render_template('login.html')

@app.route('/connect_bank/<email>')
def connect_bank(email):
    user_id = f"user_{abs(hash(email)) % 100000}"  # Safe, unique ID

    request_data = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id=user_id),
        client_name="QuitBet",
        products=[Products("transactions")],
        country_codes=[CountryCode("US")],
        language="en"
    )
    response = plaid_client.link_token_create(request_data)
    link_token = response.link_token
    return render_template('connect_bank.html', link_token=link_token, email=email)

@app.route('/exchange_token', methods=['POST'])
def exchange_token():
    data = request.json
    public_token = data['public_token']
    email = data['email']

    exchange_request = ItemPublicTokenExchangeRequest(public_token=public_token)
    exchange_response = plaid_client.item_public_token_exchange(exchange_request)
    access_token = exchange_response.access_token

    # Store the token
    if email in users:
        users[email]['access_token'] = access_token
        save_users(users)  # 🔄 Save to file

    return jsonify({'message': '✅ Bank connected successfully!'})


def generate_gemini_insight(email, txns, gambling_txns):
    if not txns:
        return "No recent transactions found."

    if gambling_txns:
        prompt = f"""
        You're QuitBet, an AI trained to help people quit gambling.
        
        User: {email}
        Here are recent transactions:
        {txns}
        
        These appear to be gambling-related:
        {gambling_txns}
        
        Generate a 1–2 sentence helpful reflection or alert, like:
        “You bet $300 this week, mostly after 10 PM. Let’s talk about setting limits.”
        
        Be supportive, helpful, and motivational.
        """
    else:
        prompt = f"""
        You're QuitBet, an AI trained to support people trying to quit gambling.
        
        User: {email}
        Here are recent transactions:
        {txns}
        
        No gambling activity was detected.
        
        Generate a short encouraging message like:
        “Great job staying on track! You’ve made 7 healthy choices this week.”
        
        Your tone should be warm, motivating, and a little personalized.
        """

    model = genai.GenerativeModel("models/gemini-1.5-pro")  # or "gemini-1.5-flash"
    response = model.generate_content(prompt)
    return response.text



def generate_gemini_checkin(email, days_clean):
    prompt = f"""
    You're QuitBet, an AI coach helping users quit gambling.
    
    This user is on day {days_clean} of their clean streak.
    
    Write a short, friendly, motivational daily check-in message. Be personal, positive, and ask a reflective question.
    
    Examples:
    - "It's Day 3 clean, Stanley! What's one thing that helped you resist yesterday?"
    - "4 days strong — incredible. Want to reflect on your proudest moment this week?"
    
    Limit to 1–2 sentences.
    """

    model = genai.GenerativeModel("models/gemini-1.5-pro")
    response = model.generate_content(prompt)
    return response.text



@app.route('/transactions/<email>')
def get_transactions(email):
    access_token = users.get(email, {}).get('access_token')
    if not access_token:
        return "❌ No access token for this user", 400

    start_date = date.today() - timedelta(days=30)
    end_date = date.today()

    request = TransactionsGetRequest(
        access_token=access_token,
        start_date=start_date,
        end_date=end_date,
        options=TransactionsGetRequestOptions(count=20)
    )

    response = plaid_client.transactions_get(request)
    txns = response['transactions']
    # if email == "winning@example.com":
    txns.append({
        "name": "DraftKings Sportsbook",
        "amount": 120.00,
        "date": "2025-04-05",
        "winnings" : 100
    })
    txns.append({
        "name": "FanDuel",
        "amount": 85.50,
        "date": "2025-04-06",
        "winnings" : -85.50
    })

    gambling_txns = [
        txn for txn in txns
        if any(kw in txn['name'].lower() for kw in GAMBLING_KEYWORDS)
    ]

    # Load progress for user (or initialize if missing)
    progress = users[email].get("progress", {
        "last_gambling_date": None,
        "days_clean": 0,
        "money_saved": 0.0
    })

    today = datetime.today().date()
    # Use user-specific estimate, or default to 40
    # Use saved estimate if exists, or assign random and save it
    if "daily_spend_estimate" not in users[email]:
        users[email]["daily_spend_estimate"] = random.randint(30, 100)
        save_users(users)  # Save updated estimate permanently

    estimate = users[email]["daily_spend_estimate"]

    if gambling_txns:
        # Update last gambling date and reset streak
        progress["last_gambling_date"] = today.isoformat()
        progress["days_clean"] = 0
        progress["money_saved"] = 0.0
    else:
        last_date = progress.get("last_gambling_date")
        if last_date:
            days_since = (today - datetime.fromisoformat(last_date).date()).days
        else:
            days_since = 1  # First day if no previous gambling

        # Only update if it's a new clean day
        if days_since > progress["days_clean"]:
            progress["days_clean"] = days_since
            progress["money_saved"] = days_since * estimate

    # Save updated progress to users.json
    users[email]["progress"] = progress
    save_users(users)

    ai_insight = generate_gemini_insight(email, txns, gambling_txns)

    # Gemini Daily Check-in Message
    today_str = today.isoformat()
    last_check = progress.get("last_checkin_date")

    if last_check != today_str:
        daily_checkin = generate_gemini_checkin(email, progress["days_clean"])
        progress["last_checkin_date"] = today_str
        save_users(users)
    else:
        daily_checkin = None

    return render_template(
        "transactions.html",
        daily_spend_estimate=estimate,

        transactions=txns,
        gambling=gambling_txns,
        email=email,
        insight=ai_insight,
        days_clean=progress["days_clean"],
        money_saved=progress["money_saved"],
        daily_checkin=daily_checkin,

    )


if __name__ == '__main__':
    app.run(debug=True)
