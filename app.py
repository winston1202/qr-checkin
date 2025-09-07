# No changes needed to imports
from flask import Flask, request, redirect, render_template, session, url_for, flash, make_response
import uuid
from datetime import datetime
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os
# At the top of app.py, add wraps from functools
from functools import wraps

app = Flask(__name__)
# Load secret key from environment variables for security
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise ValueError("A SECRET_KEY must be set in the environment variables.")

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

# --- Helper Functions ---
def get_day_with_suffix(d):
    return f"{d}{'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')}"

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash("You must be logged in to view the admin dashboard.", "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Core Check-In/Out Logic ---
def prepare_action(worker_name):
    log_values = log_sheet.get_all_values()
    if not log_values:
        raise Exception("The 'Time Clock' sheet is empty. It must have at least a header row.")

    # CORRECTED: Use exact headers without converting to lowercase
    headers = [h.strip() for h in log_values[0]]
    records = log_values[1:]

    try:
        # CORRECTED: Use exact, case-sensitive header names
        name_col_idx = headers.index("Name")
        date_col_idx = headers.index("Date")
        clock_in_col_idx = headers.index("Clock In")
        clock_out_col_idx = headers.index("Clock Out")
        verified_col_idx = headers.index("Verified")
    except ValueError as e:
        raise Exception(f"A required column is missing in 'Time Clock'. Checked for '{e.args[0]}'. Please ensure all required headers exist.")

    user_cell = users_sheet.find(worker_name, in_column=1)
    user_row_number = user_cell.row if user_cell else None
    actual_token = request.cookies.get('device_token')
    verification_status = "No"

    allow_new_user_token = session.pop('allow_new_user_token', False)

    if user_row_number:
        expected_token = users_sheet.cell(user_row_number, 2).value # Assumes token is in column 2
        if expected_token and expected_token == actual_token:
            verification_status = "Yes"
        elif not expected_token and actual_token:
            users_sheet.update_cell(user_row_number, 2, actual_token)
            verification_status = "Yes"
    else:
        if allow_new_user_token and actual_token:
            users_sheet.append_row([worker_name, actual_token])
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
        'verified': verification_status,
        'col_indices': {
            'Clock In': clock_in_col_idx + 1, 'Clock Out': clock_out_col_idx + 1,
            'Verified': verified_col_idx + 1, 'Date': date_col_idx + 1, 'Name': name_col_idx + 1
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
    message = "You have already completed your entry for the day."
    session['final_status'] = {'message': message, 'status_type': 'already_complete'}
    return redirect(url_for('success'))

# --- User-Facing Routes ---
@app.route("/")
def home():
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

    token_cell = users_sheet.find(actual_token, in_column=2)
    if token_cell:
        correct_name = users_sheet.cell(token_cell.row, 1).value
        if correct_name.strip().lower() != attempted_name.strip().lower():
            session['typo_conflict'] = {'correct_name': correct_name, 'attempted_name': attempted_name}
            return redirect(url_for('handle_typo'))
        worker_name = correct_name
    else:
        user_cell = users_sheet.find(attempted_name, in_column=1)
        if user_cell:
            flash(f"The name <strong>{attempted_name}</strong> is already registered to a different device. "
                  f"Please use your registered device or contact an administrator to update it.")
            return redirect(url_for('scan'))
        
        session['new_user_registration'] = {'name': attempted_name}
        return redirect(url_for('register'))

    prepare_action(worker_name)
    pending = session.get('pending_action', {})
    if pending.get('type') == 'Already Clocked Out':
        return handle_already_clocked_out(worker_name)
    return redirect(url_for('confirm'))

@app.route("/register", methods=["GET", "POST"])
def register():
    new_user_data = session.get('new_user_registration')
    if not new_user_data:
        return redirect(url_for('scan'))

    if request.method == 'POST':
        choice = request.form.get('choice')
        worker_name = new_user_data['name']
        session.pop('new_user_registration', None)

        if choice == 'yes':
            session['allow_new_user_token'] = True
            prepare_action(worker_name)
            return redirect(url_for('confirm'))
        else:
            flash("Registration cancelled. Please re-enter your name.")
            return redirect(url_for('scan'))
    return render_template("register.html", new_name=new_user_data['name'])

@app.route("/handle_typo", methods=["GET", "POST"])
def handle_typo():
    conflict = session.get('typo_conflict')
    if not conflict:
        return redirect(url_for('scan'))

    if request.method == 'POST':
        choice = request.form.get('choice')
        session.pop('typo_conflict', None)
        if choice == 'yes':
            prepare_action(conflict['correct_name'])
            return redirect(url_for('confirm'))
        else:
            flash(f"Incorrect name. This device is registered to <strong>{conflict['correct_name']}</strong>. Please enter the correct name to proceed.")
            return redirect(url_for('scan'))
    return render_template("handle_typo.html", correct_name=conflict['correct_name'])

@app.route("/confirm")
def confirm():
    pending = session.get('pending_action')
    if not pending:
        return redirect(url_for('scan'))
    return render_template("confirm.html", action_type=pending['type'], worker_name=pending['name'])

@app.route("/execute", methods=["POST"])
def execute():
    action = session.pop('pending_action', None)
    if not action:
        return redirect(url_for('scan'))

    action_type = action.get('type')
    cols = action.get('col_indices', {})

    if action_type == 'Clock Out':
        log_sheet.update_cell(action['row_to_update'], cols['Clock Out'], action['time'])
        log_sheet.update_cell(action['row_to_update'], cols['Verified'], action['verified'])
        message = "You have been clocked out successfully."
        status_type = 'clock_out'
    elif action_type == 'Clock In':
        num_cols = len(log_sheet.get_all_values()[0])
        new_row_data = [""] * num_cols
        new_row_data[cols['Date'] - 1] = action['date']
        new_row_data[cols['Name'] - 1] = action['name']
        new_row_data[cols['Clock In'] - 1] = action['time']
        new_row_data[cols['Verified'] - 1] = action['verified']
        log_sheet.append_row(new_row_data, value_input_option='USER_ENTERED')
        message = "You have been clocked in successfully."
        status_type = 'clock_in'
    else: 
        message = "Action processed."
        status_type = 'default'

    session['final_status'] = {'message': message, 'status_type': status_type}
    return redirect(url_for('success'))

@app.route("/success")
def success():
    final_status = session.pop('final_status', {})
    message = final_status.get('message', "Action completed successfully.")
    status_type = final_status.get('status_type', 'default')
    return render_template("success.html", message=message, status_type=status_type)


# ===============================================================
# == ADMIN SECTION ==============================================
# ===============================================================

# --- Admin Authentication ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == 'POST':
        username = request.form.get("username")
        password = request.form.get("password")
        admin_user = os.environ.get("ADMIN_USERNAME", "admin")
        admin_pass = os.environ.get("ADMIN_PASSWORD", "password")
        if username == admin_user and password == admin_pass:
            session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        else:
            flash("Invalid username or password. Please try again.", "error")
            return redirect(url_for('login'))
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop('is_admin', None)
    flash("You have been successfully logged out.", "success")
    return redirect(url_for('login'))

# --- Admin Dashboard (Tab 1) ---
@app.route("/admin")
@admin_required
def admin_redirect():
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    all_logs = log_sheet.get_all_records() # Reads headers correctly
    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    
    clocked_in_today = {}
    log_values = log_sheet.get_all_values()[1:] # Raw values list
    headers = log_sheet.get_all_values()[0]   # Raw headers list

    for i, row_list in enumerate(log_values):
        record = dict(zip(headers, row_list)) # Create dict with correct headers
        record['row_id'] = i + 2 
        if record.get('Date') == today_date and record.get('Clock In') and not record.get('Clock Out'):
            clocked_in_today[record.get('Name')] = record
    
    return render_template("admin_dashboard.html", currently_in=list(clocked_in_today.values()))

# --- Admin Time Clock Log (Tab 2) ---
@app.route("/admin/time_log")
@admin_required
def admin_time_log():
    all_users = users_sheet.get_all_records()
    unique_names = sorted(list(set(user['Name'] for user in all_users)))
    
    log_values = log_sheet.get_all_values()
    headers = log_values[0]
    all_logs_raw = log_values[1:]

    filter_name = request.args.get('name', '')
    filter_date = request.args.get('date', '')

    filtered_logs = []
    for i in range(len(all_logs_raw) - 1, -1, -1):
        log_dict = dict(zip(headers, all_logs_raw[i]))
        log_dict['row_id'] = i + 2
        
        name_matches = (not filter_name) or (filter_name == log_dict.get('Name'))
        
        date_matches = True
        if filter_date:
            try:
                sheet_date_str = log_dict.get('Date', '').replace('st,', ',').replace('nd,', ',').replace('rd,', ',').replace('th,', ',')
                sheet_date = datetime.strptime(sheet_date_str, "%b. %d, %Y").date()
                filter_dt = datetime.strptime(filter_date, "%Y-%m-%d").date()
                date_matches = (sheet_date == filter_dt)
            except (ValueError, TypeError):
                date_matches = False

        if name_matches and date_matches:
            filtered_logs.append(log_dict)

    return render_template("admin_time_log.html", 
                           logs=filtered_logs, 
                           unique_names=unique_names,
                           filter_name=filter_name,
                           filter_date=filter_date)

# --- Admin User Management (Tab 3) ---
@app.route("/admin/users")
@admin_required
def admin_users():
    users_with_ids = []
    user_records = users_sheet.get_all_records()
    for i, user in enumerate(user_records):
        user['row_id'] = i + 2
        users_with_ids.append(user)
    return render_template("admin_users.html", users=users_with_ids)

# --- Admin Action Routes ---
@app.route("/admin/fix_clock_out/<int:row_id>", methods=["POST"])
@admin_required
def fix_clock_out(row_id):
    worker_name = request.form.get("name")
    try:
        now = datetime.now(CENTRAL_TIMEZONE)
        current_time = now.strftime("%I:%M:%S %p")
        # CORRECTED: Find the capitalized header
        clock_out_col = log_sheet.find("Clock Out").col
        log_sheet.update_cell(row_id, clock_out_col, current_time)
        flash(f"Successfully clocked out {worker_name}.", 'success')
    except Exception as e:
        flash(f"Error updating clock out: {e}", "error")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route("/admin/delete_log_entry/<int:row_id>", methods=["POST"])
@admin_required
def delete_log_entry(row_id):
    try:
        log_sheet.delete_rows(row_id)
        flash("Time entry deleted successfully.", "success")
    except Exception as e:
        flash(f"Error deleting entry: {e}", "error")
    return redirect(url_for('admin_time_log'))

@app.route("/admin/add_user", methods=["POST"])
@admin_required
def add_user():
    name = request.form.get("name", "").strip()
    if name and not users_sheet.find(name, in_column=1):
        # CORRECTED: Header for new row should match sheet
        users_sheet.append_row([name, ''])
        flash(f"User '{name}' added successfully.", 'success')
    else:
        flash(f"Error: User '{name}' already exists or name is invalid.", 'error')
    return redirect(url_for('admin_users'))

@app.route("/admin/delete_user/<int:row_id>", methods=["POST"])
@admin_required
def delete_user(row_id):
    try:
        users_sheet.delete_rows(row_id)
        flash("User deleted successfully.", "success")
    except Exception as e:
        flash(f"Error deleting user: {e}", "error")
    return redirect(url_for('admin_users'))

@app.route("/admin/clear_token/<int:row_id>", methods=["POST"])
@admin_required
def clear_user_token(row_id):
    try:
        # Assumes token is in the 2nd column
        users_sheet.update_cell(row_id, 2, "")
        flash("User's device token has been cleared. They can now register a new device.", "success")
    except Exception as e:
        flash(f"Error clearing token: {e}", "error")
    return redirect(url_for('admin_users'))