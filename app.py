from flask import Flask, request, redirect, render_template, session, url_for, flash
import uuid
from datetime import datetime
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import os

app = Flask(__name__)
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
    raise Exception("Could not find worksheets 'Attendance' or 'Users'. Please check names.")

CENTRAL_TIMEZONE = pytz.timezone("America/Chicago")

def get_day_with_suffix(d):
    if 11 <= d <= 13: return f"{d}th"
    if d % 10 == 1: return f"{d}st"
    if d % 10 == 2: return f"{d}nd"
    if d % 10 == 3: return f"{d}rd"
    return f"{d}th"

def prepare_action(worker_name):
    user_cell = users_sheet.find(worker_name, in_column=1)
    if user_cell is None:
        num_data_rows = len(users_sheet.get_all_records())
        user_row_number = num_data_rows + 2
        users_sheet.append_row([worker_name, ""])
    else:
        user_row_number = user_cell.row

    expected_token = users_sheet.cell(user_row_number, 2).value
    actual_token = session.get('device_token')
    verification_status = "No"
    if not expected_token:
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

    for i, record in reversed(list(enumerate(log_records))):
        if record.get("Name") == worker_name and record.get("Date") == today_date:
            if not record.get("Clock Out"):
                row_to_update = i + 2
                break
            else:
                already_clocked_out = True
                break

    pending_action = {
        'name': worker_name, 'date': today_date, 'time': current_time,
        'verified': verification_status, 'user_row': user_row_number
    }

    if already_clocked_out:
        pending_action['type'] = 'Already Clocked Out'
    elif row_to_update:
        pending_action['type'] = 'Clock Out'
        pending_action['row_to_update'] = row_to_update
        original_status = log_sheet.cell(row_to_update, 5).value
        pending_action['combined_status'] = f"{original_status} / {verification_status}"
    else:
        pending_action['type'] = 'Clock In'

    session['pending_action'] = pending_action

@app.route("/")
def home():
    device_token = session.get('device_token')
    if device_token:
        token_cell = users_sheet.find(device_token, in_column=2)
        if token_cell is not None:
            worker_name = users_sheet.cell(token_cell.row, 1).value
            prepare_action(worker_name)
            pending = session.get('pending_action', {})
            if pending.get('type') == 'Already Clocked Out':
                session['final_status'] = {
                    'message': f"<h2>Action Completed</h2><p>{worker_name}, you have already completed your entry for the day.</p>",
                    'type': 'Already Clocked Out'
                }
                return redirect(url_for('success'))
            elif pending.get('type') == 'Clock Out': # Use Clock Out, not Offer Clock Out
                return redirect(url_for('offer_clock_out'))
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
        return "First and Last Name are required.", 400

    attempted_name = f"{first_name} {last_name}"
    user_cell = users_sheet.find(attempted_name, in_column=1)

    if user_cell:
        worker_name = attempted_name
    else:
        actual_token = session.get('device_token')
        if actual_token:
            token_cell = users_sheet.find(actual_token, in_column=2)
            if token_cell:
                correct_name = users_sheet.cell(token_cell.row, 1).value
                session['typo_conflict'] = {'correct_name': correct_name, 'attempted_name': attempted_name}
                return redirect(url_for('handle_typo'))
        worker_name = attempted_name

    prepare_action(worker_name)
    pending = session.get('pending_action', {})
    if pending.get('type') == 'Already Clocked Out':
        session['final_status'] = {
            'message': f"<h2>Action Completed</h2><p>{worker_name}, you have already completed your entry for the day.</p>",
            'type': 'Already Clocked Out'
        }
        return redirect(url_for('success'))

    # Logic is now simpler, always go to confirm page if not clocked out
    return redirect(url_for('confirm'))

@app.route("/handle_typo", methods=["GET", "POST"])
def handle_typo():
    conflict = session.get('typo_conflict')
    if not conflict:
        return redirect(url_for('scan'))

    if request.method == 'POST':
        choice = request.form.get('choice')
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
    pending = session.get('pending_action')
    if not pending:
        return redirect(url_for('scan'))

    # We can now handle both Clock In and Clock Out with this one confirmation screen
    return render_template("confirm.html", action_type=pending['type'], worker_name=pending['name'])

# The offer_clock_out route is now redundant and can be removed, but we keep it for clarity
# or future use. The main flow will use /confirm
@app.route("/offer_clock_out")
def offer_clock_out():
    pending = session.get('pending_action')
    if not pending or pending.get('type') != 'Clock Out':
        return redirect(url_for('scan'))
    return render_template("offer_clock_out.html", worker_name=pending['name'])

@app.route("/execute", methods=["POST"])
def execute():
    action = session.pop('pending_action', None)
    if not action:
        return redirect(url_for('scan'))

    if 'device_token_to_set' in session:
        token = session.pop('device_token_to_set')
        session['device_token'] = token
        users_sheet.update_cell(action['user_row'], 2, token)

    message = ""
    if action['type'] == 'Clock Out':
        log_sheet.update_cell(action['row_to_update'], 4, action['time'])
        log_sheet.update_cell(action['row_to_update'], 5, action['verified'])
        # The message now uses "Goodbye" as requested
        message = f"<h2>Goodbye, {action['name']}!</h2><p>You have been clocked out successfully.</p>"
    else: # Clock In
        new_row = [action['date'], action['name'], action['time'], "", action['verified']]
        log_sheet.append_row(new_row)
        message = f"<h2>Welcome, {action['name']}!</h2><p>You have been clocked in successfully.</p>"

    session['final_status'] = {'message': message, 'type': action['type']}
    # All actions now go to the success page
    return redirect(url_for('success'))

@app.route("/success")
def success():
    final_status = session.pop('final_status', {})
    message = final_status.get('message', '<p>Action completed.</p>')
    action_type = final_status.get('type')

    # Decide whether to show the "Back to Check-in" button
    show_back_button = True
    if action_type == 'Clock Out' or action_type == 'Already Clocked Out':
        show_back_button = False

    return render_template("success.html", message=message, show_back_button=show_back_button)