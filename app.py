# No changes needed to imports
from flask import Flask, request, redirect, render_template, session, url_for, flash, make_response, jsonify
import uuid
from datetime import datetime
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os
import time
# At the top of app.py, add wraps from functools
from functools import wraps
# Imports required for the distance function
from math import radians, sin, cos, sqrt, atan2

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
    settings_sheet = client.open("QR Check-Ins").worksheet("Settings")
except (json.JSONDecodeError, gspread.exceptions.GSpreadException) as e:
    raise Exception(f"Could not connect to Google Sheets. Please check your credentials and sheet names. Error: {e}")

CENTRAL_TIMEZONE = pytz.timezone("America/Chicago")

# Simple in-memory cache for settings
settings_cache = {}
settings_last_fetched = 0

# --- Helper Functions ---
def get_day_with_suffix(d):
    return f"{d}{'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')}"

def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculates the distance between two GPS coordinates in meters using the Haversine formula."""
    R = 6371000  # Radius of Earth in meters
    lat1_rad, lon1_rad = radians(lat1), radians(lon1)
    lat2_rad, lon2_rad = radians(lat2), radians(lon2)
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = sin(dlat / 2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    distance = R * c
    return distance

def get_settings():
    """ Fetches settings from the Google Sheet with a 60-second cache. """
    global settings_cache, settings_last_fetched
    if (time.time() - settings_last_fetched) > 60:
        try:
            settings_records = settings_sheet.get_all_records()
            settings_cache = {item['SettingName']: item['SettingValue'] for item in settings_records}
            settings_last_fetched = time.time()
        except Exception as e:
            print(f"ERROR: Could not fetch settings from Google Sheet: {e}")
            return settings_cache
    return settings_cache

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash("You must be logged in to view the admin dashboard.", "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Core Check-In/Out Logic (Unchanged) ---
def prepare_action(worker_name):
    # ... This entire function remains the same ...
    log_values = log_sheet.get_all_values()
    headers = [h.strip() for h in log_values[0]]
    records = log_values[1:]
    try:
        name_col_idx = headers.index("Name")
        date_col_idx = headers.index("Date")
        clock_in_col_idx = headers.index("Clock In")
        clock_out_col_idx = headers.index("Clock Out")
        verified_col_idx = headers.index("Verified")
    except ValueError as e:
        raise Exception(f"A required column is missing in 'Time Clock'. Checked for '{e.args[0]}'.")
    user_cell = users_sheet.find(worker_name, in_column=1)
    user_row_number = user_cell.row if user_cell else None
    actual_token = request.cookies.get('device_token')
    verification_status = "No"
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
    session['final_status'] = {'message': message, 'status_type': 'already_complete', 'worker_name': worker_name}
    return redirect(url_for('success'))

# --- User-Facing Routes ---
@app.route("/")
def home():
    # ... Unchanged ...
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
    # ... Unchanged ...
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
    # ... Unchanged ...
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
    # ... Unchanged ...
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
            flash(f"Incorrect name. This device is registered to <strong>{conflict['correct_name']}</strong>.")
            return redirect(url_for('scan'))
    return render_template("handle_typo.html", correct_name=conflict['correct_name'])

# ===============================================================
# == THIS IS THE CORRECTED /confirm FUNCTION ====================
# ===============================================================
@app.route("/confirm")
def confirm():
    pending = session.get('pending_action')
    if not pending:
        return redirect(url_for('scan'))

    settings = get_settings()
    location_check_required = settings.get('LocationVerificationEnabled') == 'TRUE'

    # === THIS IS THE FIX: These two lines were missing ===
    user_lat_str = request.args.get('lat')
    user_lon_str = request.args.get('lon')

    if location_check_required:
        if not user_lat_str or not user_lon_str:
            return redirect(url_for('enable_location'))
        
        try:
            building_lat = float(os.environ.get("BUILDING_LATITUDE"))
            building_lon = float(os.environ.get("BUILDING_LONGITUDE"))
            user_lat = float(user_lat_str)
            user_lon = float(user_lon_str)
            
            ALLOWED_RADIUS_FEET = 500
            METERS_TO_FEET = 3.28084
            distance_in_meters = calculate_distance(building_lat, building_lon, user_lat, user_lon)
            distance_in_feet = distance_in_meters * METERS_TO_FEET

            if distance_in_feet > ALLOWED_RADIUS_FEET:
                fail_message = f"You are too far away. You must be within {ALLOWED_RADIUS_FEET} feet to proceed."
                return redirect(url_for('location_failed', message=fail_message))

        except (TypeError, ValueError, AttributeError):
            return redirect(url_for('location_failed', message="Could not verify location due to a configuration error."))
        
    return render_template("confirm.html", 
                           action_type=pending['type'], 
                           worker_name=pending['name'],
                           location_verified=(user_lat_str is not None))

@app.route("/execute", methods=["POST"])
def execute():
    # ... Unchanged ...
    action = session.pop('pending_action', None)
    if not action:
        return redirect(url_for('scan'))
    action_type = action.get('type')
    cols = action.get('col_indices', {})
    worker_name = action.get('name')
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
        message = "You have been clocked in successfully. You may now close this page or clock out below."
        status_type = 'clock_in'
    else: 
        message = "Action processed."
        status_type = 'default'
    session['final_status'] = {'message': message, 'status_type': status_type, 'worker_name': worker_name}
    return redirect(url_for('success'))

@app.route("/success")
def success():
    # ... Unchanged ...
    final_status = session.pop('final_status', {})
    message = final_status.get('message', "Action completed successfully.")
    status_type = final_status.get('status_type', 'default')
    worker_name = final_status.get('worker_name')
    return render_template("success.html", message=message, status_type=status_type, worker_name=worker_name)

@app.route("/quick_clock_out", methods=["POST"])
def quick_clock_out():
    # ... Unchanged ...
    worker_name = request.form.get("worker_name")
    if not worker_name:
        flash("Could not identify the user to clock out.", "error")
        return redirect(url_for('scan'))
    log_values = log_sheet.get_all_values()
    headers = log_values[0]
    records = log_values[1:]
    try:
        name_col_idx = headers.index("Name")
        date_col_idx = headers.index("Date")
        clock_out_col_idx = headers.index("Clock Out")
    except ValueError as e:
        flash(f"A required column is missing in the sheet: {e}", "error")
        return redirect(url_for('scan'))
    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    current_time = now.strftime("%I:%M:%S %p")
    row_to_update = None
    for i, record in reversed(list(enumerate(records))):
        if record[name_col_idx] == worker_name and record[date_col_idx] == today_date and not record[clock_out_col_idx].strip():
            row_to_update = i + 2
            break
    if row_to_update:
        log_sheet.update_cell(row_to_update, clock_out_col_idx + 1, current_time)
        message = "You have been clocked out successfully."
        session['final_status'] = {'message': message, 'status_type': 'clock_out', 'worker_name': worker_name}
    else:
        message = "You have already been clocked out for the day."
        session['final_status'] = {'message': message, 'status_type': 'already_complete', 'worker_name': worker_name}
    return redirect(url_for('success'))

@app.route("/location_failed")
def location_failed():
    # ... Unchanged ...
    message = request.args.get('message', 'An unknown error occurred.')
    return render_template("location_failed.html", message=message)

@app.route("/enable_location")
def enable_location():
    # ... Unchanged ...
    return render_template("enable_location.html")

# ===============================================================
# == ADMIN SECTION (Unchanged) ==================================
# ===============================================================
# ... All admin routes remain the same as your last working version ...

# ===============================================================
# == ADMIN SECTION (Unchanged) ==================================
# ===============================================================

# --- Admin Authentication ---
@app.route("/login", methods=["GET", "POST"])
def login():
    # ... Unchanged ...
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
    # ... Unchanged ...
    session.pop('is_admin', None)
    flash("You have been successfully logged out.", "success")
    return redirect(url_for('login'))

# --- Admin Dashboard Routes ---
@app.route("/admin")
@admin_required
def admin_redirect():
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    # ... Unchanged ...
    all_logs = log_sheet.get_all_records()
    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    clocked_in_today = {}
    log_values = log_sheet.get_all_values()[1:]
    headers = log_sheet.get_all_values()[0]
    for i, row_list in enumerate(log_values):
        record = dict(zip(headers, row_list))
        record['row_id'] = i + 2 
        if record.get('Date') == today_date and record.get('Clock In') and not record.get('Clock Out'):
            clocked_in_today[record.get('Name')] = record
    return render_template("admin_dashboard.html", currently_in=list(clocked_in_today.values()))

# In app.py, replace the entire admin_time_log function with this one.

@app.route("/admin/time_log")
@admin_required
def admin_time_log():
    all_users = users_sheet.get_all_records()
    unique_names = sorted(list(set(user.get('Name', '') for user in all_users if user.get('Name'))))
    
    log_values = log_sheet.get_all_values()
    headers = log_values[0]
    all_logs_raw = log_values[1:]

    filter_name = request.args.get('name', '')
    filter_date = request.args.get('date', '')

    filtered_logs = []
    # Iterate in reverse to show newest first
    for i in range(len(all_logs_raw) - 1, -1, -1):
        log_dict = dict(zip(headers, all_logs_raw[i]))
        log_dict['row_id'] = i + 2
        
        # === THIS IS THE FIX: More robust checks ===

        # 1. Skip any row that doesn't even have a name.
        if not log_dict.get('Name'):
            continue

        # 2. Apply name filter
        name_matches = (not filter_name) or (filter_name == log_dict.get('Name'))
        
        # 3. Apply date filter safely
        date_matches = True
        if filter_date:
            try:
                # Get the date string safely, default to empty string if None
                sheet_date_str = log_dict.get('Date') or ''
                # Clean the string
                cleaned_date_str = sheet_date_str.replace('st,', ',').replace('nd,', ',').replace('rd,', ',').replace('th,', ',')
                # Parse the dates
                sheet_date = datetime.strptime(cleaned_date_str, "%b. %d, %Y").date()
                filter_dt = datetime.strptime(filter_date, "%Y-%m-%d").date()
                date_matches = (sheet_date == filter_dt)
            except (ValueError, TypeError):
                # If any part of the date processing fails, it's not a match.
                date_matches = False

        if name_matches and date_matches:
            filtered_logs.append(log_dict)

    return render_template("admin_time_log.html", 
                           logs=filtered_logs, 
                           unique_names=unique_names,
                           filter_name=filter_name,
                           filter_date=filter_date)
@app.route("/admin/users")
@admin_required
def admin_users():
    # ... Unchanged ...
    users_with_ids = []
    user_records = users_sheet.get_all_records()
    for i, user in enumerate(user_records):
        user['row_id'] = i + 2
        users_with_ids.append(user)
    return render_template("admin_users.html", users=users_with_ids)

@app.route("/admin/settings")
@admin_required
def admin_settings():
    # ... Unchanged ...
    current_settings = get_settings()
    return render_template("admin_settings.html", settings=current_settings)

@app.route("/admin/update_settings", methods=["POST"])
@admin_required
def update_settings():
    # ... Unchanged ...
    global settings_last_fetched
    setting_name = request.form.get("setting_name")
    new_value = "TRUE" if request.form.get("setting_value") == "on" else "FALSE"
    try:
        cell = settings_sheet.find(setting_name)
        settings_sheet.update_cell(cell.row, cell.col + 1, new_value)
        settings_last_fetched = 0
        flash(f"Setting '{setting_name}' updated successfully.", "success")
    except Exception as e:
        flash(f"Error updating setting: {e}", "error")
    return redirect(url_for('admin_settings'))

@app.route("/admin/fix_clock_out/<int:row_id>", methods=["POST"])
def fix_clock_out(row_id):
    # ... Unchanged ...
    worker_name = request.form.get("name")
    try:
        now = datetime.now(CENTRAL_TIMEZONE)
        current_time = now.strftime("%I:%M:%S %p")
        clock_out_col = log_sheet.find("Clock Out").col
        log_sheet.update_cell(row_id, clock_out_col, current_time)
        flash(f"Successfully clocked out {worker_name}.", 'success')
    except Exception as e:
        flash(f"Error updating clock out: {e}", "error")
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route("/admin/delete_log_entry/<int:row_id>", methods=["POST"])
def delete_log_entry(row_id):
    # ... Unchanged ...
    try:
        log_sheet.delete_rows(row_id)
        flash("Time entry deleted successfully.", "success")
    except Exception as e:
        flash(f"Error deleting entry: {e}", "error")
    return redirect(url_for('admin_time_log'))

@app.route("/admin/add_user", methods=["POST"])
def add_user():
    # ... Unchanged ...
    name = request.form.get("name", "").strip()
    if name and not users_sheet.find(name, in_column=1):
        users_sheet.append_row([name, ''])
        flash(f"User '{name}' added successfully.", 'success')
    else:
        flash(f"Error: User '{name}' already exists or name is invalid.", 'error')
    return redirect(url_for('admin_users'))

@app.route("/admin/delete_user/<int:row_id>", methods=["POST"])
def delete_user(row_id):
    # ... Unchanged ...
    try:
        users_sheet.delete_rows(row_id)
        flash("User deleted successfully.", "success")
    except Exception as e:
        flash(f"Error deleting user: {e}", "error")
    return redirect(url_for('admin_users'))

@app.route("/admin/clear_token/<int:row_id>", methods=["POST"])
def clear_user_token(row_id):
    # ... Unchanged ...
    try:
        users_sheet.update_cell(row_id, 2, "")
        flash("User's device token has been cleared. They can now register a new device.", "success")
    except Exception as e:
        flash(f"Error clearing token: {e}", "error")
    return redirect(url_for('admin_users'))

@app.route("/admin/api/dashboard_data")
@admin_required
def admin_api_dashboard_data():
    # ... Unchanged ...
    all_logs = log_sheet.get_all_records()
    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    clocked_in_today = {}
    log_values = log_sheet.get_all_values()[1:]
    headers = log_sheet.get_all_values()[0]
    for i, row_list in enumerate(log_values):
        record = dict(zip(headers, row_list))
        record['row_id'] = i + 2
        if record.get('Date') == today_date and record.get('Clock In') and not record.get('Clock Out'):
            clean_record = {
                'Name': record.get('Name'),
                'Clock In': record.get('Clock In'),
                'row_id': record.get('row_id')
            }
            clocked_in_today[record.get('Name')] = clean_record
    return jsonify(list(clocked_in_today.values()))