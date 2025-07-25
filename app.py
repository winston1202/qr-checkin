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

try:
    log_sheet = client.open("QR Check-Ins").worksheet("Attendance")
    users_sheet = client.open("QR Check-Ins").worksheet("Users")
except gspread.exceptions.WorksheetNotFound:
    raise Exception("Could not find worksheets 'Attendance' or 'Users'. Please check names in your Google Sheet.")

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

@app.route("/scan", methods=["GET"])
def scan():
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
    <form action="{{ url_for('process') }}" method="POST" class="space-y-4">
      <input name="first_name" placeholder="First Name" required
        class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400" />
      <input name="last_name" placeholder="Last Name" required
        class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400" />
      <button type="submit"
        class="bg-blue-500 text-white px-4 py-2 rounded-lg hover:bg-blue-600 w-full">Submit</button>
    </form>
  </div>
</body>
</html>
""")

# This route now handles the complex logic of checking for typos
@app.route("/process", methods=["POST"])
def process():
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    
    if not first_name or not last_name:
        return "First and Last Name are required.", 400
    
    attempted_name = f"{first_name} {last_name}"
    
    # --- Check for Typo Conflict ---
    try:
        # Check if the name they typed exists
        users_sheet.find(attempted_name, in_column=1)
        # If it exists, there's no conflict. Proceed normally.
        worker_name = attempted_name
        
    except gspread.exceptions.CellNotFound:
        # The typed name is NEW. Now, check if their DEVICE is old.
        actual_token = session.get('device_token')
        if actual_token:
            try:
                token_cell = users_sheet.find(actual_token, in_column=2)
                # CONFLICT! The device is known, but the name is not.
                correct_name = users_sheet.cell(token_cell.row, 1).value
                # Store the conflict info and go to the special confirmation page
                session['typo_conflict'] = {
                    'correct_name': correct_name,
                    'attempted_name': attempted_name
                }
                return redirect(url_for('handle_typo'))
            except gspread.exceptions.CellNotFound:
                # This is a genuinely new user with a new device.
                worker_name = attempted_name
        else:
            # A new user with no device token yet.
            worker_name = attempted_name

    # If we get here, there was no typo conflict, proceed to confirmation
    prepare_action(worker_name)
    return redirect(url_for('confirm'))

# New route to handle the typo confirmation
@app.route("/handle_typo", methods=["GET", "POST"])
def handle_typo():
    conflict = session.get('typo_conflict')
    if not conflict:
        return redirect(url_for('scan'))

    if request.method == 'POST':
        choice = request.form.get('choice')
        if choice == 'yes':
            # User confirmed they are the original person
            worker_name = conflict['correct_name']
        else: # Choice is 'no'
            # User wants to create a new profile.
            # We must clear the old device token to avoid conflicts.
            old_user_cell = users_sheet.find(conflict['correct_name'], in_column=1)
            users_sheet.update_cell(old_user_cell.row, 2, "") # Clear old token
            worker_name = conflict['attempted_name']

        session.pop('typo_conflict', None)
        prepare_action(worker_name)
        return redirect(url_for('confirm'))

    # Display the special confirmation page
    return render_template_string(f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script><title>Verify Identity</title>
</head>
<body class="bg-gray-100 h-screen flex items-center justify-center">
  <div class="bg-white p-8 rounded-xl shadow-md text-center w-full max-w-md">
    <h1 class="text-2xl font-bold mb-4">Identity Check</h1>
    <p class="text-lg text-gray-700 mb-6">This device is registered to <strong>{conflict['correct_name']}</strong>. <br><br>Are you this person?</p>
    <div class="flex justify-center space-x-4">
        <form action="{{ url_for('handle_typo') }}" method="POST">
            <input type="hidden" name="choice" value="yes">
            <button type="submit" class="bg-green-500 text-white font-bold px-6 py-2 rounded-lg hover:bg-green-600">Yes, that's me</button>
        </form>
        <form action="{{ url_for('handle_typo') }}" method="POST">
            <input type="hidden" name="choice" value="no">
            <button type="submit" class="bg-red-500 text-white font-bold px-6 py-2 rounded-lg hover:bg-red-600">No, I'm new</button>
        </form>
    </div>
  </div>
</body>
</html>
""")

# Helper function to avoid repeating code
def prepare_action(worker_name):
    # This function contains the logic from the old /process route
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
        session['device_token_to_set'] = new_token
        verification_status = "Yes"
    elif expected_token == actual_token:
        verification_status = "Yes"
        
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
            
    pending_action = {
        'name': worker_name, 'date': today_date, 'time': current_time,
        'verified': verification_status, 'user_row': user_cell.row
    }
    
    if row_to_update:
        pending_action['type'] = 'Clock Out'
        pending_action['row_to_update'] = row_to_update
        original_status = log_sheet.cell(row_to_update, 5).value
        pending_action['combined_status'] = f"{original_status} / {verification_status}"
    else:
        pending_action['type'] = 'Clock In'

    session['pending_action'] = pending_action

@app.route("/confirm", methods=["GET", "POST"])
def confirm():
    pending_action = session.get('pending_action')
    if not pending_action:
        return redirect(url_for('scan'))

    if request.method == 'POST':
        action = session.pop('pending_action', None)
        if not action: return redirect(url_for('scan'))

        if 'device_token_to_set' in session:
            token = session.pop('device_token_to_set')
            session['device_token'] = token
            users_sheet.update_cell(action['user_row'], 2, token)

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
            <button type="submit" class="bg-green-500 text-white font-bold px-6 py-2 rounded-lg hover:bg-green-600">Yes, Confirm</button>
        </form>
        <a href="{{ url_for('scan') }}" class="bg-red-500 text-white font-bold px-6 py-2 rounded-lg hover:bg-red-600">Cancel</a>
    </div>
  </div>
</body>
</html>
""")

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