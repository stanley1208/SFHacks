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


def generate_gemini_insight(email, gambling_txns):
    if not gambling_txns:
        return f"✅ Great job, {email.split('@')[0]} — no gambling-related transactions found recently!"

    # Construct input for Gemini
    total = sum(txn['amount'] for txn in gambling_txns)
    count = len(gambling_txns)
    summary_lines = [f"- {txn['date']}: {txn['name']} (${txn['amount']})" for txn in gambling_txns]

    prompt = f"""
    The following is a list of recent gambling-related transactions for a user:
    Total: {count} transactions
    Amount spent: ${total:.2f}
    Dates & Merchants:
    {' '.join(summary_lines)}

    As an AI assistant for gambling recovery, generate a supportive, non-judgmental insight or reflection based on these transactions.
    Keep it short (1–2 sentences) and actionable. Be gentle and motivational.
    """

    model = genai.GenerativeModel("models/gemini-1.5-pro")

    response = model.generate_content(prompt)
    return response.text.strip()


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
    if email == "winning@example.com":
        txns.append({
            "name": "DraftKings Sportsbook",
            "amount": 120.00,
            "date": "2025-04-05"
        })
        txns.append({
            "name": "FanDuel",
            "amount": 85.50,
            "date": "2025-04-06"
        })

    gambling_txns = [
        txn for txn in txns
        if any(kw in txn['name'].lower() for kw in GAMBLING_KEYWORDS)
    ]

    ai_insight = generate_gemini_insight(email, gambling_txns)

    return render_template("transactions.html", transactions=txns, gambling=gambling_txns, email=email, insight=ai_insight)


if __name__ == '__main__':
    app.run(debug=True)
