# No changes needed to imports
from flask import Flask, request, redirect, render_template, session, url_for, flash
import uuid
from datetime import datetime
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os
from flask import Flask, request, redirect, render_template, session, url_for, flash, make_response

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
    log_sheet = client.open("QR Check-Ins").worksheet("Time Clock")
    users_sheet = client.open("QR Check-Ins").worksheet("Users")
except (json.JSONDecodeError, gspread.exceptions.GSpreadException) as e:
    raise Exception(f"Could not connect to Google Sheets. Please check your credentials and sheet names. Error: {e}")

CENTRAL_TIMEZONE = pytz.timezone("America/Chicago")

def get_day_with_suffix(d):
    return f"{d}{'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')}"


def prepare_action(worker_name):
    log_values = log_sheet.get_all_values()
    if not log_values:
        raise Exception("The 'Time Clock' sheet is empty. It must have at least a header row.")

    headers = [h.strip().lower() for h in log_values[0]]
    records = log_values[1:]

    try:
        name_col_idx = headers.index("name")
        date_col_idx = headers.index("date")
        clock_in_col_idx = headers.index("clock in")
        clock_out_col_idx = headers.index("clock out")
        verified_col_idx = headers.index("verified")
    except ValueError as e:
        raise Exception(f"A required column is missing in 'Time Clock'. Checked for '{e.args[0]}'. Please ensure all required headers exist.")

    user_cell = users_sheet.find(worker_name, in_column=1)
    user_row_number = user_cell.row if user_cell else None
    actual_token = request.cookies.get('device_token')
    verification_status = "No"

    # Only associate token for new users if explicitly allowed (after confirmation)
    allow_new_user_token = session.pop('allow_new_user_token', False)

    if user_row_number:
        expected_token = users_sheet.cell(user_row_number, 2).value
        if expected_token and expected_token == actual_token:
            verification_status = "Yes"
        elif not expected_token and actual_token:
            users_sheet.update_cell(user_row_number, 2, actual_token)
            verification_status = "Yes"
    else:
        if allow_new_user_token and actual_token:
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
    # Show a dedicated success page for already clocked out
    message = f"<h2>{worker_name}, you have already completed your entry for the day.</h2>"
    session['final_status'] = {'message': message, 'type': 'Already Clocked Out'}
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
    actual_token = request.cookies.get('device_token')

    if not actual_token:
        flash("Your browser could not be identified. Please enable cookies and try again.")
        return redirect(url_for('scan'))

    # Case 1: Is this a recognized device?
    token_cell = users_sheet.find(actual_token, in_column=2)
    if token_cell:
        correct_name = users_sheet.cell(token_cell.row, 1).value
        if correct_name.strip().lower() != attempted_name.strip().lower():
            session['typo_conflict'] = {'correct_name': correct_name, 'attempted_name': attempted_name}
            return redirect(url_for('handle_typo'))
        worker_name = correct_name

    # Case 2: This is an unrecognized device.
    else:
        user_cell = users_sheet.find(attempted_name, in_column=1)
        if user_cell:
            flash(f"The name <strong>{attempted_name}</strong> is already registered to a different device. "
                  f"Please use your registered device. If this is a new device, "
                  f"contact an administrator to get it updated.")
            return redirect(url_for('scan'))
        
        # --- THIS IS THE KEY CHANGE ---
        # It's a new user. Send them to the dedicated registration confirmation page.
        # We use a different session variable to keep it separate from typo conflicts.
        session['new_user_registration'] = {'name': attempted_name}
        return redirect(url_for('register')) # <-- DIRECTS TO THE NEW ROUTE

    # If we get here, the user is valid. Proceed to confirmation.
    prepare_action(worker_name)
    pending = session.get('pending_action', {})
    if pending.get('type') == 'Already Clocked Out':
        return handle_already_clocked_out(worker_name)
    
    return redirect(url_for('confirm'))

@app.route("/register", methods=["GET", "POST"])
def register():
    """
    Handles the confirmation screen for a new user to register their device.
    """
    new_user_data = session.get('new_user_registration')
    if not new_user_data:
        # If there's no data, they shouldn't be here. Send them to the start.
        return redirect(url_for('scan'))

    if request.method == 'POST':
        choice = request.form.get('choice')
        worker_name = new_user_data['name']
        session.pop('new_user_registration', None) # Clear the session data

        if choice == 'yes':
            # User confirmed their name. Now we can allow the token to be associated.
            session['allow_new_user_token'] = True
            prepare_action(worker_name)
            return redirect(url_for('confirm'))
        else:
            # User clicked "No", they made a typo. Send them back to fix it.
            flash("Registration cancelled. Please re-enter your name.")
            return redirect(url_for('scan'))

    # For a GET request, just show the confirmation page.
    return render_template("register.html", new_name=new_user_data['name'])

@app.route("/handle_typo", methods=["GET", "POST"])
def handle_typo():
    """
    Handles the screen where a user must confirm their identity if their
    entered name doesn't match the name registered to their device token.
    """
    conflict = session.get('typo_conflict')
    if not conflict:
        # If there's no conflict data, they shouldn't be here. Send them to the start.
        return redirect(url_for('scan'))

    if request.method == 'POST':
        choice = request.form.get('choice')
        is_new_user = conflict.get('new_user', False)
        session.pop('typo_conflict', None)

        if choice == 'yes':
            worker_name = conflict['correct_name']
            if is_new_user:
                # Only now, after confirmation, allow token association
                session['allow_new_user_token'] = True
            prepare_action(worker_name)
            return redirect(url_for('confirm'))
        else:
            if is_new_user:
                # For new users, just go back to scan
                return redirect(url_for('scan'))
            flash(f"Incorrect name. This device is registered to <strong>{conflict['correct_name']}</strong>. Please enter the correct name to proceed.")
            return redirect(url_for('scan'))

    # If it's a GET request, this is the first time the user is seeing the page.
    # Just show them the identity check screen.
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
        # Use a solid blue text color for the name, matching other screens, and same size as 'Goodbye,'
        message = f"""
        <h2>Goodbye, <span style='color:#3b82f6;'>{action['name']}</span>!</h2>
        <p>You have been clocked out successfully.</p>"""
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
    # Only show back button if not a Clock In (welcome), Clock Out, or Already Clocked Out
    show_back_button = action_type not in ['Clock In', 'Clock Out', 'Already Clocked Out']
    # Remove back button for all success screens
    show_back_button = False
    return render_template("success.html", message=message, show_back_button=show_back_button)