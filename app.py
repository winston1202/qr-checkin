from flask import Flask, request, redirect, render_template, session, url_for, flash
import uuid
from datetime import datetime
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os

app = Flask(__name__)
# It's crucial to set a secret key for session management.
# For production, use an environment variable.
app.secret_key = os.environ.get("SECRET_KEY", "a-default-secret-key-for-development")
if app.secret_key == "a-default-secret-key-for-development":
    print("Warning: Using default SECRET_KEY. Please set a proper secret key in your environment for production.")


# --- Google Sheets Setup ---
# Ensure the GOOGLE_SHEETS_CREDS environment variable is set with your JSON credentials.
creds_json_string = os.environ.get("GOOGLE_SHEETS_CREDS")
if not creds_json_string:
    raise Exception("Missing GOOGLE_SHEETS_CREDS environment variable.")

try:
    creds_dict = json.loads(creds_json_string)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    # Open the workbook and worksheets.
    log_sheet = client.open("QR Check-Ins").worksheet("Attendance")
    users_sheet = client.open("QR Check-Ins").worksheet("Users")
except (json.JSONDecodeError, gspread.exceptions.GSpreadException) as e:
    raise Exception(f"Could not connect to Google Sheets. Please check your credentials and sheet names. Error: {e}")

CENTRAL_TIMEZONE = pytz.timezone("America/Chicago")

def get_day_with_suffix(d):
    """Formats the day of the month with the correct suffix (e.g., 1st, 2nd, 3rd, 4th)."""
    return f"{d}{'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')}"

def prepare_action(worker_name):
    """
    Checks the user's status in Google Sheets and determines the next required action.
    Returns: A dictionary with the pending action details.
    """
    user_cell = users_sheet.find(worker_name, in_column=1)
    if user_cell:
        user_row_number = user_cell.row
    else:
        # If user does not exist, add them to the Users sheet.
        users_sheet.append_row([worker_name, ""])
        user_row_number = len(users_sheet.get_all_records()) + 1

    # Check if this device is verified for the user.
    expected_token = users_sheet.cell(user_row_number, 2).value
    actual_token = session.get('device_token')
    verification_status = "No"
    if not expected_token:
        # If no token is associated, assign this new device to the user.
        new_token = str(uuid.uuid4())
        session['device_token_to_set'] = new_token
        verification_status = "Yes"
    elif expected_token == actual_token:
        verification_status = "Yes"

    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    current_time = now.strftime("%I:%M:%S %p")

    log_records = log_sheet.get_all_records()
    row_to_update = None
    already_clocked_out = False

    # Search backwards through the logs to find the user's last action for today.
    for i, record in reversed(list(enumerate(log_records))):
        if record.get("Name") == worker_name and record.get("Date") == today_date:
            if record.get("Clock Out"):
                already_clocked_out = True
            else:
                row_to_update = i + 2  # +2 to convert from 0-based index to 1-based gspread row.
            break # Found the last relevant record for today.

    pending_action = {
        'name': worker_name, 'date': today_date, 'time': current_time,
        'verified': verification_status, 'user_row': user_row_number
    }

    if already_clocked_out:
        pending_action['type'] = 'Already Clocked Out'
    elif row_to_update:
        pending_action['type'] = 'Clock Out'
        pending_action['row_to_update'] = row_to_update
    else:
        pending_action['type'] = 'Clock In'

    session['pending_action'] = pending_action


@app.route("/")
def home():
    """
    Handles returning users with an existing session token.
    Redirects them to the appropriate action based on their status.
    """
    device_token = session.get('device_token')
    if device_token:
        token_cell = users_sheet.find(device_token, in_column=2)
        if token_cell:
            worker_name = users_sheet.cell(token_cell.row, 1).value
            prepare_action(worker_name)
            pending = session.get('pending_action', {})

            if pending.get('type') == 'Already Clocked Out':
                # If user has already clocked in and out, show final success page.
                session['final_status'] = {
                    'message': f"<h2>Action Completed</h2><p>{worker_name}, you have already completed your entry for the day.</p>",
                    'type': 'Already Clocked Out'
                }
                return redirect(url_for('success'))
            else:
                # For both 'Clock In' and 'Clock Out' actions, go to the generic confirmation page.
                return redirect(url_for('confirm'))

    # If no valid token, send to the scanning/login page.
    return redirect(url_for('scan'))


@app.route("/scan")
def scan():
    """Renders the initial page for scanning a QR code or entering a name."""
    # This template should be named 'scan.html'
    return render_template("scan.html")


@app.route("/process", methods=["POST"])
def process():
    """
    Processes a user's name submitted via form.
    """
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()

    if not first_name or not last_name:
        flash("First and Last Name are required.")
        return redirect(url_for('scan'))

    worker_name = f"{first_name} {last_name}"
    prepare_action(worker_name)
    pending = session.get('pending_action', {})

    if pending.get('type') == 'Already Clocked Out':
        session['final_status'] = {
            'message': f"<h2>Action Completed</h2><p>{worker_name}, you have already completed your entry for the day.</p>",
            'type': 'Already Clocked Out'
        }
        return redirect(url_for('success'))

    # All other cases ('Clock In' or 'Clock Out') go to the confirm page.
    return redirect(url_for('confirm'))


@app.route("/confirm")
def confirm():
    """
    Shows a generic confirmation screen for any pending action ('Clock In' or 'Clock Out').
    """
    pending = session.get('pending_action')
    if not pending:
        return redirect(url_for('scan'))

    # This template should be named 'confirm.html'
    return render_template("confirm.html", action_type=pending['type'], worker_name=pending['name'])


@app.route("/execute", methods=["POST"])
def execute():
    """
    Executes the pending action (Clock In or Clock Out) and updates the Google Sheet.
    """
    action = session.pop('pending_action', None)
    if not action:
        return redirect(url_for('scan'))

    # If a new device was used, save its token to the Users sheet.
    if 'device_token_to_set' in session:
        token = session.pop('device_token_to_set')
        session['device_token'] = token
        users_sheet.update_cell(action['user_row'], 2, token)

    message = ""
    action_type = action.get('type')

    if action_type == 'Clock Out':
        log_sheet.update_cell(action['row_to_update'], 4, action['time']) # Update Clock Out time
        log_sheet.update_cell(action['row_to_update'], 5, action['verified']) # Update Verified status
        message = f"<h2>Goodbye, {action['name']}!</h2><p>You have been clocked out successfully.</p>"
    elif action_type == 'Clock In':
        new_row = [action['date'], action['name'], action['time'], "", action['verified']]
        log_sheet.append_row(new_row)
        message = f"<h2>Welcome, {action['name']}!</h2><p>You have been clocked in successfully.</p>"

    # Store the final message and action type to show on the success page.
    session['final_status'] = {'message': message, 'type': action_type}
    return redirect(url_for('success'))


@app.route("/success")
def success():
    """
    Displays a final success/status message to the user.
    The "Back to Check-in" button is hidden if the user's day is complete.
    """
    final_status = session.pop('final_status', None)
    if not final_status:
        # If someone navigates here directly, just show a generic message.
        return render_template("success.html", message="<p>Action completed.</p>", show_back_button=True)

    message = final_status.get('message')
    action_type = final_status.get('type')

    # The button is hidden for a completed clock-out or if they were already done.
    show_back_button = action_type not in ['Clock Out', 'Already Clocked Out']

    # This template should be named 'success.html' and is provided in the prompt.
    return render_template("success.html", message=message, show_back_button=show_back_button)

