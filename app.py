from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_cors import CORS
from dotenv import load_dotenv
import os
import json
import google.generativeai as genai
from datetime import date, datetime, timedelta
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
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

@app.route('/survey/<email>', methods=['GET', 'POST'])
def show_survey(email):
    if request.method == 'POST':
        actual = float(request.form["actual"])
        goal = float(request.form["goal"])

        # Save in users
        users[email]["weekly_checkin"] = {
            "actual_spent": actual,
            "goal_to_save": goal,
            "limit": 500  # fixed cap
        }
        save_users(users)

        return redirect(url_for('get_transactions', email=email))

    return render_template("survey.html", email=email)


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


def generate_gemini_insight(email, txns, gambling_txns, total_spent, net_winnings, net_loss):
    # Format the gambling transaction summary
    gambling_summary = "\n".join(
        f"- {txn['name']} on {txn['date']}: ${txn['amount']:.2f}" for txn in gambling_txns
    )

    # Format the net stats clearly
    financial_summary = (
        f"\nTotal spent on gambling: ${total_spent:.2f}"
        f"\nTotal winnings: ${net_winnings:.2f}"
        f"\nNet loss: ${net_loss:.2f}"
    )

    # Construct the full prompt
    prompt = (
        f"The following are gambling-related transactions for the user {email}:\n\n"
        f"{gambling_summary if gambling_summary else 'No gambling transactions found.'}\n\n"
        f"Summary of gambling finances:\n{financial_summary}\n\n"
        f"Please generate a personalized and empathetic insight for the user. "
        f"Address their current gambling behavior in a supportive tone, "
        f"and mention the net loss if relevant. Encourage them to reflect and seek help if needed."
    )


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



def generate_gemini_question(email, transactions, days_clean):
    simplified_txns = []
    for txn in transactions[-5:]:
        try:
            date_val = txn["date"]
            if isinstance(date_val, date):  # If it's a datetime.date object
                date_val = date_val.isoformat()

            simplified_txns.append({
                "name": txn["name"],
                "amount": txn["amount"],
                "date": date_val
            })
        except Exception:
            simplified_txns.append({
                "name": getattr(txn, "name", "N/A"),
                "amount": getattr(txn, "amount", "N/A"),
                "date": str(getattr(txn, "date", "N/A"))
            })

    prompt = f"""
    You're QuitBet, an AI assistant helping people quit gambling.

    You are about to ask the user a **personalized question** based on their recent activity.

    Here are the last few transactions:
    {json.dumps(simplified_txns, indent=2)}

    They are currently on day {days_clean} of their clean streak.

    Generate one short, meaningful question to help them reflect on their behavior, progress, or emotions. Use a supportive tone.
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

    txn_request = TransactionsGetRequest(
        access_token=access_token,
        start_date=start_date,
        end_date=end_date,
        options=TransactionsGetRequestOptions(count=20)
    )

    reflect_state = request.args.get("reflect", "ask")

    response = plaid_client.transactions_get(txn_request)
    txns = response['transactions']

    # Add fake gambling transactions for demo/testing purposes
    fake_gambling_sources = [
        "DraftKings Sportsbook", "FanDuel", "BetMGM", "Caesars Casino", "PokerStars",
        "PointsBet", "Barstool Sportsbook", "WSOP Online", "Golden Nugget Casino"
    ]

    if email.endswith("@example.com"):  # only add for test/demo users
        for _ in range(10):  # exactly 10 transactions
            vendor = random.choice(fake_gambling_sources)
            amount = round(random.uniform(10, 500), 2)
            days_ago = random.randint(1, 30)
            txns.append({
                "name": vendor,
                "amount": amount,
                "date": (date.today() - timedelta(days=days_ago)).isoformat(),
                "winnings": round(random.uniform(-amount, amount), 2)
            })

    gambling_txns = [
        txn for txn in txns
        if any(kw in txn['name'].lower() for kw in GAMBLING_KEYWORDS)
    ]

    # Calculate gambling stats
    total_spent = sum(txn["amount"] for txn in gambling_txns)
    net_winnings = sum(txn.get("winnings", 0.0) for txn in gambling_txns)
    net_loss = total_spent - net_winnings

    # Calculate average daily spend based on unique days
    gambling_dates = {txn["date"] for txn in gambling_txns}
    avg_daily_spend = round(total_spent / len(gambling_dates), 2) if gambling_dates else 0.0

    # Load progress for user (or initialize if missing)
    progress = users[email].get("progress", {
        "last_gambling_date": None,
        "days_clean": 0,
        "money_saved": 0.0
    })

    today = datetime.today().date()
    # Daily Estimated Spend is the daily average spent on gambling of user.

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
            progress["money_saved"] = days_since * avg_daily_spend

    # Save updated progress to users.json
    users[email]["progress"] = progress
    users[email]["daily_spend_estimate"] = avg_daily_spend
    save_users(users)

    # Convert all transaction dates to datetime.date objects
    for txn in txns:
        if isinstance(txn['date'], str):
            txn['date'] = datetime.fromisoformat(txn['date']).date()

    # Sort transactions by date (newest first)
    txns.sort(key=lambda x: x['date'], reverse=True)

    # Sort gambling transactions by date (newest first)
    gambling_txns.sort(key=lambda x: x['date'], reverse=True)

    ai_insight = generate_gemini_insight(
        email=email,
        txns=txns,
        gambling_txns=gambling_txns,
        total_spent=total_spent,
        net_winnings=net_winnings,
        net_loss=net_loss
    )

    reflect_state = request.args.get("reflect", "ask")  # "yes", "no", or "ask"

    # Only generate a question if the user agrees
    if reflect_state == "yes":
        personal_question = generate_gemini_question(email, txns, progress["days_clean"])
    else:
        personal_question = None

    # Gemini Daily Check-in Message
    today_str = today.isoformat()
    last_check = progress.get("last_checkin_date")

    if last_check != today_str:
        daily_checkin = generate_gemini_checkin(email, progress["days_clean"])
        progress["last_checkin_date"] = today_str
        save_users(users)
    else:
        daily_checkin = None


    # Reuse existing filtered gambling_txns list
    chart_data = [
        {
            "date": txn["date"].isoformat() if isinstance(txn["date"], date) else txn["date"],
            "amount": txn["amount"]
        }
        for txn in gambling_txns
    ]

    return render_template(
        "transactions.html",
        daily_spend_estimate=avg_daily_spend,
        transactions=txns,
        gambling=gambling_txns,
        email=email,
        insight=ai_insight,
        days_clean=progress["days_clean"],
        money_saved=progress["money_saved"],
        daily_checkin=daily_checkin,
        personal_question=personal_question,
        reflect_state=reflect_state,
        chart_data=json.dumps(chart_data),
    )

@app.route('/answer_question/<email>', methods=['POST'])
def answer_question(email):
    user_response = request.form.get("response")
    print(f"[{email}] responded: {user_response}")
    return redirect(url_for('get_transactions', email=email))

if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=True, host="0.0.0.0", port=port)


