# No changes needed to imports
from flask import Flask, request, redirect, render_template, session, url_for, flash
import uuid
from datetime import datetime
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "a-default-secret-key-for-development")
# ... (rest of the setup code is the same) ...
# --- Google Sheets Setup ---
creds_json_string = os.environ.get("GOOGLE_SHEETS_CREDS")
if not creds_json_string:
    raise Exception("Missing GOOGLE_SHEETS_CREDS environment variable.")

try:
    creds_dict = json.loads(creds_json_string)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    log_sheet = client.open("QR Check-Ins").worksheet("Attendance")
    users_sheet = client.open("QR Check-Ins").worksheet("Users")
except (json.JSONDecodeError, gspread.exceptions.GSpreadException) as e:
    raise Exception(f"Could not connect to Google Sheets. Please check your credentials and sheet names. Error: {e}")

CENTRAL_TIMEZONE = pytz.timezone("America/Chicago")

def get_day_with_suffix(d):
    return f"{d}{'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')}"


def prepare_action(worker_name):
    log_values = log_sheet.get_all_values()
    if not log_values:
        raise Exception("The 'Attendance' sheet is empty. It must have at least a header row.")

    headers = [h.strip().lower() for h in log_values[0]]
    records = log_values[1:]

    try:
        name_col_idx = headers.index("name")
        date_col_idx = headers.index("date")
        clock_in_col_idx = headers.index("clock in")
        clock_out_col_idx = headers.index("clock out")
        verified_col_idx = headers.index("verified")
    except ValueError as e:
        raise Exception(f"A required column is missing in 'Attendance'. Checked for '{e.args[0]}'. Please ensure all required headers exist.")

    user_cell = users_sheet.find(worker_name, in_column=1)
    user_row_number = user_cell.row if user_cell else None
    
    # ★★★ FIX: Read the token from the regular browser cookie, not the session ★★★
    actual_token = request.cookies.get('device_token')
    verification_status = "No"

    if user_row_number:
        expected_token = users_sheet.cell(user_row_number, 2).value
        # If the user has a token in the sheet, check if it matches the browser's token
        if expected_token and expected_token == actual_token:
            verification_status = "Yes"
        # If the user has NO token in the sheet, we associate the browser's token with them.
        elif not expected_token and actual_token:
            users_sheet.update_cell(user_row_number, 2, actual_token)
            verification_status = "Yes"
    else: # This is a new user
        if actual_token:
            users_sheet.append_row([worker_name, actual_token])
            user_row_number = len(users_sheet.get_all_records()) + 1
            verification_status = "Yes"

    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    current_time = now.strftime("%I:%M:%S %p")

    row_to_update = None
    already_clocked_out = False

    for i, record in reversed(list(enumerate(records))):
        if len(record) > max(name_col_idx, date_col_idx, clock_out_col_idx) and record[name_col_idx] == worker_name and record[date_col_idx] == today_date:
            clock_out_value = record[clock_out_col_idx]
            if clock_out_value and clock_out_value.strip():
                already_clocked_out = True
            else:
                row_to_update = i + 2
            break
            
    pending_action = {
        'name': worker_name, 'date': today_date, 'time': current_time,
        'verified': verification_status, 'user_row': user_row_number,
        'col_indices': {
            'name': name_col_idx + 1, 'date': date_col_idx + 1,
            'clock_in': clock_in_col_idx + 1, 'clock_out': clock_out_col_idx + 1,
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

    session['pending_action'] = pending_action # Use session just to pass data between requests

def handle_already_clocked_out(worker_name):
    # This function remains the same
    session['final_status'] = {
        'message': f"<h2>Action Completed</h2><p>{worker_name}, you have already completed your entry for the day.</p>",
        'type': 'Already Clocked Out'
    }
    return redirect(url_for('success'))

@app.route("/")
def home():
    # ★★★ FIX: Read from request.cookies instead of session ★★★
    device_token = request.cookies.get('device_token')
    if device_token:
        token_cell = users_sheet.find(device_token, in_column=2)
        if token_cell:
            worker_name = users_sheet.cell(token_cell.row, 1).value
            prepare_action(worker_name)
            pending = session.get('pending_action', {})
            if pending.get('type') == 'Already Clocked Out':
                return handle_already_clocked_out(worker_name)
            return redirect(url_for('confirm'))
    return redirect(url_for('scan'))

@app.route("/scan")
def scan():
    # Your new scan.html is used here. No changes to this function.
    return render_template("scan.html")

@app.route("/process", methods=["POST"])
def process():
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    if not first_name or not last_name:
        flash("First and Last Name are required.")
        return redirect(url_for('scan'))

    attempted_name = f"{first_name} {last_name}"
    
    # ★★★ FIX: Read from request.cookies for the identity check ★★★
    actual_token = request.cookies.get('device_token')
    if actual_token:
        token_cell = users_sheet.find(actual_token, in_column=2)
        if token_cell:
            correct_name = users_sheet.cell(token_cell.row, 1).value
            if correct_name.strip().lower() != attempted_name.strip().lower():
                session['typo_conflict'] = {'correct_name': correct_name, 'attempted_name': attempted_name}
                return redirect(url_for('handle_typo'))

    prepare_action(attempted_name)
    pending = session.get('pending_action', {})
    if pending.get('type') == 'Already Clocked Out':
        return handle_already_clocked_out(attempted_name)
    return redirect(url_for('confirm'))

@app.route("/handle_typo", methods=["GET", "POST"])
def handle_typo():
    # This function remains largely the same, but the logic inside prepare_action is now smarter.
    conflict = session.get('typo_conflict')
    if not conflict:
        return redirect(url_for('scan'))

    if request.method == 'POST':
        choice = request.form.get('choice')
        worker_name = ""
        if choice == 'yes':
            worker_name = conflict['correct_name']
        else:
            old_user_cell = users_sheet.find(conflict['correct_name'], in_column=1)
            if old_user_cell:
                users_sheet.update_cell(old_user_cell.row, 2, "")
            worker_name = conflict['attempted_name']

        session.pop('typo_conflict', None)
        prepare_action(worker_name)
        return redirect(url_for('confirm'))
        
    return render_template("handle_typo.html", correct_name=conflict['correct_name'])

@app.route("/confirm")
def confirm():
    # This function remains the same
    pending = session.get('pending_action')
    if not pending:
        return redirect(url_for('scan'))
    return render_template("confirm.html", action_type=pending['type'], worker_name=pending['name'])

@app.route("/execute", methods=["POST"])
def execute():
    action = session.pop('pending_action', None)
    if not action:
        return redirect(url_for('scan'))

    # ★★★ FIX: The server no longer creates tokens, so this entire block is removed ★★★
    # if 'device_token_to_set' in session:
    #     token = session.pop('device_token_to_set')
    #     session['device_token'] = token
    #     users_sheet.update_cell(action['user_row'], 2, token)

    action_type = action.get('type')
    cols = action.get('col_indices', {})

    if action_type == 'Clock Out':
        log_sheet.update_cell(action['row_to_update'], cols['clock_out'], action['time'])
        log_sheet.update_cell(action['row_to_update'], cols['verified'], action['verified'])
        message = f"<h2>Goodbye, {action['name']}!</h2><p>You have been clocked out successfully.</p>"
    elif action_type == 'Clock In':
        num_cols = len(log_sheet.get_all_values()[0])
        new_row_data = [""] * num_cols
        new_row_data[cols['date'] - 1] = action['date']
        new_row_data[cols['name'] - 1] = action['name']
        new_row_data[cols['clock_in'] - 1] = action['time']
        new_row_data[cols['verified'] - 1] = action['verified']
        log_sheet.append_row(new_row_data, value_input_option='USER_ENTERED')
        message = f"<h2>Welcome, {action['name']}!</h2><p>You have been clocked in successfully.</p>"

    session['final_status'] = {'message': message, 'type': action_type}
    return redirect(url_for('success'))

@app.route("/success")
def success():
    # This function remains the same
    final_status = session.pop('final_status', {})
    message = final_status.get('message', "<p>Action completed.</p>")
    action_type = final_status.get('type')
    show_back_button = action_type not in ['Clock Out', 'Already Clocked Out']
    return render_template("success.html", message=message, show_back_button=show_back_button)