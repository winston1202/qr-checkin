# ===============================================================
# == IMPORTS AND SETUP ==========================================
# ===============================================================
from flask import Flask, request, redirect, render_template, session, url_for, flash, make_response, jsonify
import uuid
from datetime import datetime
import pytz
import json
import os
import time
from functools import wraps
from math import radians, sin, cos, sqrt, atan2
import csv
import io
# NEW: Import the database tools
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise ValueError("A SECRET_KEY must be set in the environment variables.")

# NEW: Configure the database connection from the DATABASE_URL environment variable
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

CENTRAL_TIMEZONE = pytz.timezone("America/Chicago")

# ===============================================================
# == DATABASE MODELS (Our new "Tables") =========================
# ===============================================================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), unique=True, nullable=False)
    device_token = db.Column(db.String(36), unique=True, nullable=True)

class TimeLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    user_name = db.Column(db.String(100), nullable=False)
    date = db.Column(db.String(50), nullable=False)
    clock_in = db.Column(db.String(50), nullable=False)
    clock_out = db.Column(db.String(50), nullable=True)
    verified = db.Column(db.String(10), nullable=False)

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(50), nullable=False)

# This command creates the tables in your database if they don't exist
with app.app_context():
    db.create_all()
    # Create the default setting if it doesn't exist
    if not Setting.query.filter_by(name='LocationVerificationEnabled').first():
        default_setting = Setting(name='LocationVerificationEnabled', value='TRUE')
        db.session.add(default_setting)
        db.session.commit()

# ===============================================================
# == HELPER FUNCTIONS (Now using the database) ==================
# ===============================================================
def get_day_with_suffix(d):
    return f"{d}{'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')}"

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    lat1_rad, lon1_rad = radians(lat1), radians(lon1)
    lat2_rad, lon2_rad = radians(lat2), radians(lon2)
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = sin(dlat / 2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

def get_settings():
    settings_list = Setting.query.all()
    return {setting.name: setting.value for setting in settings_list}

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            flash("You must be logged in to view the admin dashboard.", "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ===============================================================
# == CORE CHECK-IN/OUT LOGIC (Now using the database) ============
# ===============================================================
def prepare_action(worker_name):
    actual_token = request.cookies.get('device_token')
    user = User.query.filter_by(name=worker_name).first()
    verification_status = "No"
    
    allow_new_user_token = session.pop('allow_new_user_token', False)

    if user:
        if user.device_token and user.device_token == actual_token:
            verification_status = "Yes"
        elif not user.device_token and actual_token:
            user.device_token = actual_token
            db.session.commit()
            verification_status = "Yes"
    elif allow_new_user_token and actual_token:
        # Find user by name and add token, or create new user
        user_to_update = User.query.filter_by(name=worker_name).first()
        if user_to_update:
            user_to_update.device_token = actual_token
        else:
            new_user = User(name=worker_name, device_token=actual_token)
            db.session.add(new_user)
        db.session.commit()
        verification_status = "Yes"
        
    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    current_time = now.strftime("%I:%M:%S %p")

    log_entry = TimeLog.query.filter_by(user_name=worker_name, date=today_date, clock_out=None).first()

    pending_action = {'name': worker_name, 'date': today_date, 'time': current_time, 'verified': verification_status}

    if log_entry:
        pending_action['type'] = 'Clock Out'
        pending_action['log_id'] = log_entry.id
    else:
        already_clocked_out = TimeLog.query.filter(TimeLog.user_name == worker_name, TimeLog.date == today_date, TimeLog.clock_out != None).first()
        if already_clocked_out:
            pending_action['type'] = 'Already Clocked Out'
        else:
            pending_action['type'] = 'Clock In'

    session['pending_action'] = pending_action

def handle_already_clocked_out(worker_name):
    message = "You have already completed your entry for the day."
    session['final_status'] = {'message': message, 'status_type': 'already_complete', 'worker_name': worker_name}
    return redirect(url_for('success'))

# ===============================================================
# == USER-FACING ROUTES (Now using the database) ================
# ===============================================================
@app.route("/")
def home():
    device_token = request.cookies.get('device_token')
    if device_token:
        user = User.query.filter_by(device_token=device_token).first()
        if user:
            prepare_action(user.name)
            pending = session.get('pending_action', {})
            if pending.get('type') == 'Already Clocked Out':
                return handle_already_clocked_out(user.name)
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

    user_by_token = User.query.filter_by(device_token=actual_token).first()
    if user_by_token:
        if user_by_token.name.strip().lower() != attempted_name.strip().lower():
            session['typo_conflict'] = {'correct_name': user_by_token.name, 'attempted_name': attempted_name}
            return redirect(url_for('handle_typo'))
        worker_name = user_by_token.name
    else:
        user_by_name = User.query.filter_by(name=attempted_name).first()
        if user_by_name:
            if user_by_name.device_token:
                flash(f"The name <strong>{attempted_name}</strong> is already registered to a different device.", "error")
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
            flash(f"Incorrect name. This device is registered to <strong>{conflict['correct_name']}</strong>.", "error")
            return redirect(url_for('scan'))
    return render_template("handle_typo.html", correct_name=conflict['correct_name'])

@app.route("/confirm")
def confirm():
    pending = session.get('pending_action')
    if not pending:
        return redirect(url_for('scan'))
    settings = get_settings()
    location_check_required = settings.get('LocationVerificationEnabled') == 'TRUE'
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
    action = session.pop('pending_action', None)
    if not action:
        return redirect(url_for('scan'))
    
    action_type = action.get('type')
    worker_name = action.get('name')

    if action_type == 'Clock Out':
        log_entry = TimeLog.query.get(action['log_id'])
        if log_entry:
            log_entry.clock_out = action['time']
            log_entry.verified = action['verified']
            db.session.commit()
        message = "You have been clocked out successfully."
        status_type = 'clock_out'
    elif action_type == 'Clock In':
        new_log = TimeLog(
            user_name=action['name'],
            date=action['date'],
            clock_in=action['time'],
            clock_out=None,
            verified=action['verified']
        )
        db.session.add(new_log)
        db.session.commit()
        message = "You have been clocked in successfully. You may now close this page or clock out below."
        status_type = 'clock_in'
    else:
        message = "Action processed."
        status_type = 'default'

    session['final_status'] = {'message': message, 'status_type': status_type, 'worker_name': worker_name}
    return redirect(url_for('success'))

@app.route("/success")
def success():
    final_status = session.pop('final_status', {})
    message = final_status.get('message', "Action completed successfully.")
    status_type = final_status.get('status_type', 'default')
    worker_name = final_status.get('worker_name')
    return render_template("success.html", message=message, status_type=status_type, worker_name=worker_name)

@app.route("/quick_clock_out", methods=["POST"])
def quick_clock_out():
    worker_name = request.form.get("worker_name")
    if not worker_name:
        flash("Could not identify the user to clock out.", "error")
        return redirect(url_for('scan'))

    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    
    log_entry = TimeLog.query.filter_by(user_name=worker_name, date=today_date, clock_out=None).first()
            
    if log_entry:
        log_entry.clock_out = now.strftime("%I:%M:%S %p")
        db.session.commit()
        message = "You have been clocked out successfully."
        session['final_status'] = {'message': message, 'status_type': 'clock_out', 'worker_name': worker_name}
    else:
        message = "You have already been clocked out for the day."
        session['final_status'] = {'message': message, 'status_type': 'already_complete', 'worker_name': worker_name}
        
    return redirect(url_for('success'))

@app.route("/location_failed")
def location_failed():
    message = request.args.get('message', 'An unknown error occurred.')
    return render_template("location_failed.html", message=message)

@app.route("/enable_location")
def enable_location():
    return render_template("enable_location.html")

# ===============================================================
# == ADMIN SECTION (Now using the database) =====================
# ===============================================================

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

@app.route("/admin")
@admin_required
def admin_redirect():
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    
    currently_in = TimeLog.query.filter(TimeLog.date == today_date, TimeLog.clock_out == None).all()
    
    return render_template("admin_dashboard.html", currently_in=currently_in)

@app.route("/admin/time_log")
@admin_required
def admin_time_log():
    all_users = User.query.with_entities(User.name).distinct().order_by(User.name).all()
    unique_names = [user.name for user in all_users]
    
    query = TimeLog.query

    filter_name = request.args.get('name', '')
    filter_date = request.args.get('date', '')
    sort_by = request.args.get('sort_by', 'date')
    sort_order = request.args.get('sort_order', 'desc')

    if filter_name:
        query = query.filter(TimeLog.user_name == filter_name)
    if filter_date:
        try:
            filter_dt = datetime.strptime(filter_date, "%Y-%m-%d")
            date_str = f"%b. {get_day_with_suffix(filter_dt.day)}, %Y"
            query = query.filter(TimeLog.date == filter_dt.strftime(date_str))
        except ValueError:
            pass

    # Sorting logic
    sort_column = getattr(TimeLog, sort_by, TimeLog.id)
    if sort_order == 'desc':
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    filtered_logs = query.all()

    return render_template("admin_time_log.html", 
                           logs=filtered_logs, 
                           unique_names=unique_names,
                           filter_name=filter_name,
                           filter_date=filter_date,
                           sort_by=sort_by,
                           sort_order=sort_order)

@app.route("/admin/users")
@admin_required
def admin_users():
    all_users = User.query.order_by(User.name).all()
    return render_template("admin_users.html", users=all_users)

@app.route("/admin/settings")
@admin_required
def admin_settings():
    current_settings = get_settings()
    return render_template("admin_settings.html", settings=current_settings)

@app.route("/admin/update_settings", methods=["POST"])
@admin_required
def update_settings():
    setting_name = request.form.get("setting_name")
    new_value = "TRUE" if request.form.get("setting_value") == "on" else "FALSE"
    
    setting = Setting.query.filter_by(name=setting_name).first()
    if setting:
        setting.value = new_value
        db.session.commit()
        flash(f"Setting '{setting_name}' updated successfully.", "success")
    else:
        flash(f"Error: Could not find setting '{setting_name}'.", "error")

    return redirect(url_for('admin_settings'))

@app.route("/admin/fix_clock_out/<int:log_id>", methods=["POST"])
@admin_required
def fix_clock_out(log_id):
    log_entry = TimeLog.query.get(log_id)
    if log_entry:
        log_entry.clock_out = datetime.now(CENTRAL_TIMEZONE).strftime("%I:%M:%S %p")
        db.session.commit()
        flash(f"Successfully clocked out {log_entry.user_name}.", 'success')
    else:
        flash("Error: Could not find the time entry to update.", 'error')
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route("/admin/delete_log_entry/<int:log_id>", methods=["POST"])
@admin_required
def delete_log_entry(log_id):
    log_entry = TimeLog.query.get(log_id)
    if log_entry:
        db.session.delete(log_entry)
        db.session.commit()
        flash("Time entry deleted successfully.", "success")
    else:
        flash("Error: Could not find the time entry to delete.", "error")
    return redirect(url_for('admin_time_log'))

@app.route("/admin/add_user", methods=["POST"])
@admin_required
def add_user():
    name = request.form.get("name", "").strip()
    if name and not User.query.filter_by(name=name).first():
        new_user = User(name=name)
        db.session.add(new_user)
        db.session.commit()
        flash(f"User '{name}' added successfully.", 'success')
    else:
        flash(f"Error: User '{name}' already exists or name is invalid.", 'error')
    return redirect(url_for('admin_users'))

@app.route("/admin/delete_user/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = User.query.get(user_id)
    if user:
        TimeLog.query.filter_by(user_name=user.name).delete()
        db.session.delete(user)
        db.session.commit()
        flash(f"User '{user.name}' and all their logs have been deleted.", 'success')
    else:
        flash("Error: Could not find user to delete.", "error")
    return redirect(url_for('admin_users'))

@app.route("/admin/clear_token/<int:user_id>", methods=["POST"])
@admin_required
def clear_user_token(user_id):
    user = User.query.get(user_id)
    if user:
        user.device_token = None
        db.session.commit()
        flash(f"User '{user.name}'s device token has been cleared.", "success")
    else:
        flash("Error: Could not find user to clear token.", "error")
    return redirect(url_for('admin_users'))

@app.route("/admin/api/dashboard_data")
@admin_required
def admin_api_dashboard_data():
    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    
    clocked_in_today = TimeLog.query.filter(TimeLog.date == today_date, TimeLog.clock_out == None).all()
    
    data = [{'Name': log.user_name, 'Clock In': log.clock_in, 'row_id': log.id} for log in clocked_in_today]
    return jsonify(data)

@app.route("/admin/export_csv")
@admin_required
def export_csv():
    query = TimeLog.query
    filter_name = request.args.get('name', '')
    filter_date = request.args.get('date', '')
    if filter_name:
        query = query.filter(TimeLog.user_name == filter_name)
    if filter_date:
        try:
            filter_dt = datetime.strptime(filter_date, "%Y-%m-%d")
            date_str = f"%b. {get_day_with_suffix(filter_dt.day)}, %Y"
            query = query.filter(TimeLog.date == filter_dt.strftime(date_str))
        except ValueError: pass

    filtered_logs = query.order_by(TimeLog.id.desc()).all()
    
    logs_for_csv = [
        {'Name': log.user_name, 'Date': log.date, 'Clock In': log.clock_in, 'Clock Out': log.clock_out, 'Verified': log.verified}
        for log in filtered_logs
    ]
    
    output = io.StringIO()
    if logs_for_csv:
        # Use the exact keys from the dictionary
        fieldnames = ['Name', 'Date', 'Clock In', 'Clock Out', 'Verified']
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(logs_for_csv)

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = f"attachment; filename=timesheet_export_{datetime.now().strftime('%Y-%m-%d')}.csv"
    response.headers["Content-type"] = "text/csv"
    return response

@app.route("/admin/print_view")
@admin_required
def admin_print_view():
    query = TimeLog.query
    filter_name = request.args.get('name', '')
    filter_date = request.args.get('date', '')
    if filter_name:
        query = query.filter(TimeLog.user_name == filter_name)
    if filter_date:
        try:
            filter_dt = datetime.strptime(filter_date, "%Y-%m-%d")
            date_str = f"%b. {get_day_with_suffix(filter_dt.day)}, %Y"
            query = query.filter(TimeLog.date == filter_dt.strftime(date_str))
        except ValueError: pass
        
    filtered_logs = query.order_by(TimeLog.id.desc()).all()
    
    generation_time = datetime.now(CENTRAL_TIMEZONE).strftime("%Y-%m-%d %I:%M %p")
    return render_template("admin_print_view.html",
                           logs=filtered_logs,
                           filter_name=filter_name,
                           filter_date=filter_date,
                           generation_time=generation_time)