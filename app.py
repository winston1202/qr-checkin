from flask import Flask, request, redirect, render_template_string, session, url_for
import uuid
from datetime import datetime
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os

app = Flask(__name__)

# The SECRET_KEY is required for using sessions securely
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise Exception("Missing SECRET_KEY environment variable.")

# --- Google Sheets Setup ---
creds_json = os.environ.get("GOOGLE_SHEETS_CREDS")
creds_dict = json.loads(creds_json)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

# ★★★ USING YOUR CORRECTED SHEET NAMES ★★★
try:
    log_sheet = client.open("QR Check-Ins").worksheet("Attendance")
    users_sheet = client.open("QR Check-Ins").worksheet("Users")
except gspread.exceptions.WorksheetNotFound:
    raise Exception("Could not find worksheets 'Attendance' or 'Users'. Please check names in your Google Sheet.")


# --- Timezone and Date Suffix Function (no changes) ---
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

# The main check-in page
@app.route("/scan", methods=["GET"])
def scan():
    return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="https://cdn.tailwindcss.com"></script>
  <title>Check-In</title>
</head>
<body class="bg-gray-100 h-screen flex items-center justify-center">
  <div class="bg-white p-6 rounded-xl shadow-md text-center w-full max-w-md">
    <h1 class="text-2xl font-bold mb-4">Worker Check-In / Out</h1>
    <form action="{{ url_for('process') }}" method="POST" class="space-y-4">
      <input name="name" placeholder="Enter your Full Name" required
        class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400" />
      <button type="submit"
        class="bg-blue-500 text-white px-4 py-2 rounded-lg hover:bg-blue-600 w-full">Submit</button>
    </form>
  </div>
</body>
</html>
""")

# ★★★ NEW: This route PREPARES the action but does not execute it ★★★
@app.route("/process", methods=["POST"])
def process():
    worker_name = request.form.get("name").strip()
    if not worker_name:
        return "Name cannot be empty.", 400
    
    # --- Auto-Create and Verify User ---
    try:
        user_cell = users_sheet.find(worker_name, in_column=1)
    except gspread.exceptions.CellNotFound:
        users_sheet.append_row([worker_name, ""])
        user_cell = users_sheet.find(worker_name, in_column=1)
        
    expected_token = users_sheet.cell(user_cell.row, 2).value
    actual_token = session.get('device_token')
    
    verification_status = "No"
    if not expected_token:
        new_token = str(uuid.uuid4())
        session['device_token_to_set'] = new_token # Prepare to set token after confirmation
        verification_status = "Yes"
    elif expected_token == actual_token:
        verification_status = "Yes"
    
    # --- Prepare Clock-In or Clock-Out Data ---
    now = datetime.now(CENTRAL_TIMEZONE)
    day_with_suffix = get_day_with_suffix(now.day)
    today_date = now.strftime(f"%b. {day_with_suffix}, %Y")
    current_time = now.strftime("%I:%M:%S %p")
    
    log_records = log_sheet.get_all_records()
    row_to_update = None
    for i, record in reversed(list(enumerate(log_records))):
        if record.get("Name") == worker_name and record.get("Date") == today_date and not record.get("Clock Out"):
            row_to_update = i + 2
            break
            
    # ★★★ Store the pending action in the session ★★★
    pending_action = {
        'name': worker_name,
        'date': today_date,
        'time': current_time,
        'verified': verification_status,
        'user_row': user_cell.row
    }
    
    if row_to_update:
        pending_action['type'] = 'Clock Out'
        pending_action['row_to_update'] = row_to_update
        original_status = log_sheet.cell(row_to_update, 5).value
        pending_action['combined_status'] = f"{original_status} / {verification_status}"
    else:
        pending_action['type'] = 'Clock In'

    session['pending_action'] = pending_action
    return redirect(url_for('confirm'))

# ★★★ NEW: The Confirmation Page Route ★★★
@app.route("/confirm", methods=["GET", "POST"])
def confirm():
    pending_action = session.get('pending_action')
    if not pending_action:
        return redirect(url_for('scan'))

    # --- This handles the FINAL confirmation ---
    if request.method == 'POST':
        # Retrieve action from session
        action = session.pop('pending_action', None)
        if not action: return redirect(url_for('scan')) # Should not happen

        # Set device token if it was a first-time registration
        if 'device_token_to_set' in session:
            token = session.pop('device_token_to_set')
            session['device_token'] = token
            users_sheet.update_cell(action['user_row'], 2, token)

        # ★★★ WRITE TO GOOGLE SHEETS HERE ★★★
        if action['type'] == 'Clock Out':
            log_sheet.update_cell(action['row_to_update'], 4, action['time'])
            log_sheet.update_cell(action['row_to_update'], 5, action['combined_status'])
            message = f"<h2>Goodbye, {action['name']}!</h2><p>Verification: {action['combined_status']}</p>"
        else: # Clock In
            new_row = [action['date'], action['name'], action['time'], "", action['verified']]
            log_sheet.append_row(new_row)
            message = f"<h2>Welcome, {action['name']}!</h2><p>Verification: {action['verified']}</p>"
            
        session['last_message'] = message
        return redirect(url_for('success'))
        
    # --- This displays the confirmation page ---
    action_type = pending_action.get('type', 'action')
    worker_name = pending_action.get('name', 'Unknown')
    
    return render_template_string(f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script><title>Confirm Action</title>
</head>
<body class="bg-gray-100 h-screen flex items-center justify-center">
  <div class="bg-white p-8 rounded-xl shadow-md text-center w-full max-w-md">
    <h1 class="text-2xl font-bold mb-4">Please Confirm</h1>
    <p class="text-lg text-gray-700 mb-6">You are about to <strong>{action_type}</strong> for <strong>{worker_name}</strong>. Is this correct?</p>
    <div class="flex justify-center space-x-4">
        <form action="{{ url_for('confirm') }}" method="POST">
            <button type="submit" class="bg-green-500 text-white font-bold px-6 py-2 rounded-lg hover:bg-green-600">
                Yes, Confirm
            </button>
        </form>
        <a href="{{ url_for('scan') }}" class="bg-red-500 text-white font-bold px-6 py-2 rounded-lg hover:bg-red-600">
            Cancel
        </a>
    </div>
  </div>
</body>
</html>
""")

# Final success page (unchanged)
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