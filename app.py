from flask import Flask, request, redirect, render_template_string
from datetime import datetime
from zoneinfo import ZoneInfo # For timezone conversion
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os

app = Flask(__name__)

# --- Google Sheets Setup (no changes here) ---
creds_json = os.environ.get("GOOGLE_SHEETS_CREDS")
if not creds_json:
    raise Exception("Missing GOOGLE_SHEETS_CREDS environment variable.")

creds_dict = json.loads(creds_json)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open("QR Check-Ins").sheet1

# --- New: Define our timezone ---
CENTRAL_TIMEZONE = ZoneInfo("America/Chicago") # Correct IANA timezone name for USA Central Time

@app.route("/")
def home():
    return redirect("/scan")

@app.route("/scan", methods=["GET", "POST"])
def scan():
    if request.method == "POST":
        name = request.form.get("name").strip() # Use .strip() to remove accidental spaces
        botcheck = request.form.get("botcheck")
        if botcheck or not name:
            return "Bot or empty name detected", 400

        # --- Timezone-aware timestamp ---
        now = datetime.now(CENTRAL_TIMEZONE)
        today_date = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M:%S")
        
        message = ""

        # --- New Clock-In/Clock-Out Logic ---
        # Get all records from the sheet to search them
        records = sheet.get_all_records()
        row_to_update = None
        
        # We iterate backwards to find the most recent entry for that name
        for i, record in reversed(list(enumerate(records))):
            # Check for a matching name and date, with an empty "Clock Out"
            if record.get("Name") == name and record.get("Date") == today_date and not record.get("Clock Out"):
                row_to_update = i + 2 # Add 2 to convert 0-based index to 1-based gspread row number
                break
        
        if row_to_update:
            # --- This is a CLOCK OUT ---
            # Find the "Clock Out" column (it's the 4th column, or 'D')
            sheet.update_cell(row_to_update, 4, current_time)
            # We also need the original clock-in time to display it
            original_clock_in_time = records[row_to_update - 2].get("Clock In")
            message = f"""
                <h2 class='text-2xl font-bold text-blue-600 mb-2'>Goodbye, {name}!</h2>
                <p class='text-gray-700'>Clocked In at: {original_clock_in_time}</p>
                <p class='text-gray-700 mb-4'>Clocked Out at: {current_time}</p>
            """
        else:
            # --- This is a CLOCK IN ---
            new_row = [today_date, name, current_time, ""] # Leave Clock Out empty
            sheet.append_row(new_row)
            message = f"""
                <h2 class='text-2xl font-bold text-green-600 mb-2'>Welcome, {name}!</h2>
                <p class='text-gray-700 mb-4'>Clocked In at: {current_time}</p>
            """

        # --- Dynamic Thank You Page ---
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
    {message}
    <a href='/scan' class='inline-block text-blue-500 hover:underline'>🔁 Check In/Out Another Person</a>
  </div>
</body>
</html>
""")

    # --- The check-in form page (no changes needed here) ---
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
    <h1 class=\"text-2xl font-bold mb-4\">Check-In / Out</h1>
    <form method=\"POST\" class=\"space-y-4\">
      <input type=\"text\" name=\"botcheck\" style=\"display:none\">
      <input name=\"name\" placeholder=\"Enter your full name\" required
        class=\"w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400\" />
      <button type=\"submit\"
        class=\"bg-blue-500 text-white px-4 py-2 rounded-lg hover:bg-blue-600 w-full\">Submit</button>
    </form>
  </div>
</body>
</html>
"""
    return render_template_string(html)