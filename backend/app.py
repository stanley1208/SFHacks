from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv
import os

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

# In-memory "database"
users = {}

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

    return jsonify({'message': '✅ Bank connected successfully!'})

if __name__ == '__main__':
    app.run(debug=True)
