from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_cors import CORS

app = Flask(__name__, template_folder='templates')
CORS(app)

users = {}

@app.route('/')
def home():
    return render_template('signup.html')  # Default to signup page

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

        return f"Welcome back, {email}!"

    return render_template('login.html')

if __name__ == '__main__':
    app.run(debug=True)
