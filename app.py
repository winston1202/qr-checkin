from flask import Flask, request, redirect, render_template_string
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os

app = Flask(__name__)

# Load Google Sheets credentials from environment variable
creds_json = os.environ.get("GOOGLE_SHEETS_CREDS")
if not creds_json:
    raise Exception("Missing GOOGLE_SHEETS_CREDS environment variable.")

# Parse credentials JSON
creds_dict = json.loads(creds_json)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

# Open the spreadsheet
sheet = client.open("QR Check-Ins").sheet1

@app.route("/")
def home():
    return redirect("/scan")

@app.route("/scan", methods=["GET", "POST"])
def scan():
    if request.method == "POST":
        name = request.form.get("name")
        botcheck = request.form.get("botcheck")
        if botcheck:
            return "Bot detected", 400

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Log to Google Sheets
        sheet.append_row([name, timestamp])

        return render_template_string(f"""
<!DOCTYPE html>
<html lang='en'>
<head>
  <meta charset='UTF-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1.0'>
  <script src='https://cdn.tailwindcss.com'></script>
  <title>Checked In</title>
</head>
<body class='bg-gray-100 h-screen flex items-center justify-center'>
  <div class='bg-white p-6 rounded-xl shadow-md text-center w-full max-w-md'>
    <h2 class='text-2xl font-bold text-green-600 mb-2'>Thanks, {name}!</h2>
    <p class='text-gray-700 mb-4'>Checked in at {timestamp}</p>
    <a href='/scan' class='inline-block text-blue-500 hover:underline'>🔁 Back to check-in</a>
  </div>
</body>
</html>
""")


    html = """
<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
  <script src=\"https://cdn.tailwindcss.com\"></script>
  <title>Check-In</title>
</head>
<body class=\"bg-gray-100 h-screen flex items-center justify-center\">
  <div class=\"bg-white p-6 rounded-xl shadow-md text-center w-full max-w-md\">
    <h1 class=\"text-2xl font-bold mb-4\">Check-In</h1>
    <form method=\"POST\" class=\"space-y-4\">
      <input type=\"text\" name=\"botcheck\" style=\"display:none\">
      <input name=\"name\" placeholder=\"Enter your name\" required
        class=\"w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400\" />
      <button type=\"submit\"
        class=\"bg-blue-500 text-white px-4 py-2 rounded-lg hover:bg-blue-600 w-full\">Check In</button>
    </form>
  </div>
</body>
</html>
"""

    return render_template_string(html)