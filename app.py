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
    This version is more robust and finds columns by header name.
    """
    # Get all data from the log sheet, including headers.
    log_values = log_sheet.get_all_values()
    if not log_values:
        raise Exception("The 'Attendance' sheet is empty. It must have at least a header row.")

    headers = [h.strip() for h in log_values[0]] # Read and clean headers
    records = log_values[1:]

    # Find column indices dynamically. This is robust against reordering.
    try:
        name_col_idx = headers.index("Name")
        date_col_idx = headers.index("Date")
        clock_in_col_idx = headers.index("Clock In")
        clock_out_col_idx = headers.index("Clock Out")
        verified_col_idx = headers.index("Verified")
    except ValueError as e:
        raise Exception(f"A required column is missing in the 'Attendance' sheet. Please ensure 'Name', 'Date', 'Clock In', 'Clock Out', and 'Verified' headers exist. Details: {e}")

    user_cell = users_sheet.find(worker_name, in_column=1)
    user_row_number = user_cell.row if user_cell else None

    # Logic to handle verification and device tokens
    verification_status = "No"
    if user_row_number:
        expected_token = users_sheet.cell(user_row_number, 2).value
        actual_token = session.get('device_token')
        if not expected_token:
            new_token = str(uuid.uuid4())
            session['device_token_to_set'] = new_token
            verification_status = "Yes"
        elif expected_token == actual_token:
            verification_status = "Yes"
    else: # New user, not yet in the Users sheet
        users_sheet.append_row([worker_name, ""])
        user_row_number = len(users_sheet.get_all_records()) + 1 # Re-fetch or calculate new row
        new_token = str(uuid.uuid4())
        session['device_token_to_set'] = new_token
        verification_status = "Yes"

    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    current_time = now.strftime("%I:%M:%S %p")

    row_to_update = None
    already_clocked_out = False

    # Search backwards through the logs to find the user's last action for today.
    for i, record in reversed(list(enumerate(records))):
        # Check if the record has enough columns to avoid index errors
        if len(record) > max(name_col_idx, date_col_idx, clock_out_col_idx):
            if record[name_col_idx] == worker_name and record[date_col_idx] == today_date:
                clock_out_value = record[clock_out_col_idx]
                if clock_out_value and clock_out_value.strip(): # Check for a non-empty string
                    already_clocked_out = True
                else:
                    row_to_update = i + 2  # +1 for header, +1 for 1-based index
                break # Found the last relevant record for today.

    pending_action = {
        'name': worker_name, 'date': today_date, 'time': current_time,
        'verified': verification_status, 'user_row': user_row_number,
        'col_indices': { # Store column indices for the execute step
            'clock_in': clock_in_col_idx + 1,
            'clock_out': clock_out_col_idx + 1,
            'verified': verified_col_idx + 1
        }
    }

    if already_clocked_out:
        pending_action['type'] = 'Already Clocked Out'
    elif row_to_update:
        pending_action['type'] = 'Clock Out'
        pending_action['row_to_update'] = row_to_update
    else:
        pending_action['type'] = 'Clock In'

    session['pending_action'] = pending_action


def handle_already_clocked_out(worker_name):
    """Prepares and redirects for the 'Already Clocked Out' case."""
    session['final_status'] = {
        'message': f"<h2>Action Completed</h2><p>{worker_name}, you have already completed your entry for the day.</p>",
        'type': 'Already Clocked Out'
    }
    return redirect(url_for('success'))


@app.route("/")
def home():
    """Handles returning users with a session token."""
    device_token = session.get('device_token')
    if device_token:
        token_cell = users_sheet.find(device_token, in_column=2)
        if token_cell:
            worker_name = users_sheet.cell(token_cell.row, 1).value
            prepare_action(worker_name)
            pending = session.get('pending_action', {})

            if pending.get('type') == 'Already Clocked Out':
                return handle_already_clocked_out(worker_name)
            else:
                return redirect(url_for('confirm'))

    return redirect(url_for('scan'))


@app.route("/scan")
def scan():
    """Renders the initial page."""
    return render_template("scan.html")


@app.route("/process", methods=["POST"])
def process():
    """Processes a user's name from the form."""
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()

    if not first_name or not last_name:
        flash("First and Last Name are required.")
        return redirect(url_for('scan'))

    worker_name = f"{first_name} {last_name}"
    prepare_action(worker_name)
    pending = session.get('pending_action', {})

    if pending.get('type') == 'Already Clocked Out':
        return handle_already_clocked_out(worker_name)

    return redirect(url_for('confirm'))


@app.route("/confirm")
def confirm():
    """Shows a generic confirmation screen for any pending action."""
    pending = session.get('pending_action')
    if not pending:
        return redirect(url_for('scan'))
    return render_template("confirm.html", action_type=pending['type'], worker_name=pending['name'])


@app.route("/execute", methods=["POST"])
def execute():
    """Executes the pending action and updates the Google Sheet."""
    action = session.pop('pending_action', None)
    if not action:
        return redirect(url_for('scan'))

    if 'device_token_to_set' in session:
        token = session.pop('device_token_to_set')
        session['device_token'] = token
        users_sheet.update_cell(action['user_row'], 2, token)

    message = ""
    action_type = action.get('type')
    cols = action.get('col_indices', {})

    if action_type == 'Clock Out':
        log_sheet.update_cell(action['row_to_update'], cols['clock_out'], action['time'])
        log_sheet.update_cell(action['row_to_update'], cols['verified'], action['verified'])
        message = f"<h2>Goodbye, {action['name']}!</h2><p>You have been clocked out successfully.</p>"
    elif action_type == 'Clock In':
        # Create a blank row with the correct number of columns based on headers
        num_cols = len(log_sheet.get_all_values()[0])
        new_row_data = [""] * num_cols
        
        # Place data in the correct columns by index
        new_row_data[cols['clock_in'] - 1] = action['time']
        new_row_data[cols['verified'] - 1] = action['verified']
        # The following assume "Date" and "Name" are the first two columns.
        # For full robustness, their indices should also be used.
        new_row_data[0] = action['date']
        new_row_data[1] = action['name']

        log_sheet.append_row(new_row_data)
        message = f"<h2>Welcome, {action['name']}!</h2><p>You have been clocked in successfully.</p>"

    session['final_status'] = {'message': message, 'type': action_type}
    return redirect(url_for('success'))


@app.route("/success")
def success():
    """Displays a final success/status message."""
    final_status = session.pop('final_status', None)
    if not final_status:
        return render_template("success.html", message="<p>Action completed.</p>", show_back_button=True)

    message = final_status.get('message')
    action_type = final_status.get('type')
    show_back_button = action_type not in ['Clock Out', 'Already Clocked Out']

    return render_template("success.html", message=message, show_back_button=show_back_button) 