from flask import Flask, request, redirect, render_template_string, session, url_for
from datetime import datetime
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os

app = Flask(__name__)

# The SECRET_KEY is still useful for the Post/Redirect/Get pattern
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise Exception("Missing SECRET_KEY environment variable.")

# --- Google Sheets Setup ---
creds_json = os.environ.get("GOOGLE_SHEETS_CREDS")
creds_dict = json.loads(creds_json)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

try:
    # We only need to open the one "Attendance" sheet
    log_sheet = client.open("QR Check-Ins").worksheet("Attendance")
except gspread.exceptions.WorksheetNotFound:
    raise Exception("Could not find a worksheet named 'Attendance'. Please check the sheet name.")

# --- Timezone and Date Suffix Function ---
CENTRAL_TIMEZONE = pytz.timezone("America/Chicago")
def get_day_with_suffix(d):
    if 11 <= d <= 13: return f"{d}th"
    if d % 10 == 1: return f"{d}st"
    if d % 10 == 2: return f"{d}nd"
    if d % 10 == 3: return f"{d}rd"
    return f"{d}th"

@app.route("/")
def home():
    return redirect(url_for('scan'))

@app.route("/scan", methods=["GET", "POST"])
def scan():
    # --- This handles the form submission ---
    if request.method == "POST":
        # We now use a single input for the full name
        worker_name = request.form.get("full_name", "").strip()
        if not worker_name:
            return "Name cannot be empty.", 400

        # --- Time and Date Logic ---
        now = datetime.now(CENTRAL_TIMEZONE)
        day_with_suffix = get_day_with_suffix(now.day)
        today_date = now.strftime(f"%b. {day_with_suffix}, %Y")
        current_time = now.strftime("%I:%M:%S %p")

        # --- Smart Clock-In/Out Logic ---
        log_records = log_sheet.get_all_records()
        row_to_update = None
        for i, record in reversed(list(enumerate(log_records))):
            # Find the most recent entry for this person on this day that isn't clocked out
            if record.get("Name") == worker_name and record.get("Date") == today_date and not record.get("Clock Out"):
                row_to_update = i + 2 # Add 2 to convert list index to gspread row number
                break
        
        message = ""
        if row_to_update:
            # This is a CLOCK OUT
            log_sheet.update_cell(row_to_update, 4, current_time) # Column D is Clock Out
            original_clock_in_time = log_records[row_to_update - 2].get("Clock In")
            message = f"<h2>Goodbye, {worker_name}!</h2><p>Clocked Out at: {current_time}</p><p><small>Original Clock In: {original_clock_in_time}</small></p>"
        else:
            # This is a CLOCK IN
            new_row = [today_date, worker_name, current_time, ""]
            log_sheet.append_row(new_row)
            message = f"<h2>Welcome, {worker_name}!</h2><p>Clocked In at: {current_time}</p>"
        
        # Store the message and redirect to the success page to prevent duplicates
        session['last_message'] = message
        return redirect(url_for('success'))

    # --- This displays the form page ---
    return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="https://cdn.tailwindcss.com"></script><title>Check-In</title>
</head>
<body class="bg-gray-100 h-screen flex items-center justify-center">
  <div class="bg-white p-6 rounded-xl shadow-md text-center w-full max-w-md">
    <h1 class="text-2xl font-bold mb-4">Worker Check-In / Out</h1>
    <form method="POST" class="space-y-4">
      <input name="full_name" placeholder="Enter your Full Name" required
        class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400" />
      <button type="submit"
        class="bg-blue-500 text-white px-4 py-2 rounded-lg hover:bg-blue-600 w-full">Submit</button>
    </form>
  </div>
</body>
</html>
""")

# Final success page
@app.route("/success")
def success():
    message = session.pop('last_message', '<p>Action completed.</p>')
    return render_template_string(f"""
<!DOCTYPE html>
<html lang='en'>
<head>
  <meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>
  <script src='https://cdn.tailwindcss.com'></script><title>Status</title>
  <style> h2 {{ font-size: 1.5rem; font-weight: bold; margin-bottom: 0.5rem; }} </style>
</head>
<body class='bg-gray-100 h-screen flex items-center justify-center'>
  <div class='bg-white p-6 rounded-xl shadow-md text-center w-full max-w-md'>
    {message}
    <a href='/scan' class='mt-4 inline-block text-blue-500 hover:underline'>🔁 Back to check-in page</a>
  </div>
</body>
</html>
""")