# ===============================================================
# == IMPORTS AND SETUP ==========================================
# ===============================================================
from flask import Flask, request, redirect, render_template, session, url_for, flash, g, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import uuid
from datetime import datetime
import pytz
import os
import time
from functools import wraps
# === IMPORTS WERE MISSING - NOW ADDED BACK ===
from math import radians, sin, cos, sqrt, atan2
from flask import Flask, request, redirect, render_template, session, url_for, flash, g, jsonify, make_response
import io
import csv

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise ValueError("A SECRET_KEY must be set in the environment variables.")

# --- Database and Security Setup ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

CENTRAL_TIMEZONE = pytz.timezone("America/Chicago")

# ===============================================================
# == DATABASE MODELS (Unchanged) ================================
# ===============================================================
class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    join_token = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    users = db.relationship('User', backref='team', lazy=True, cascade="all, delete-orphan")
    settings = db.relationship('TeamSetting', backref='team', lazy=True, cascade="all, delete-orphan")

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password = db.Column(db.String(60), nullable=True)
    role = db.Column(db.String(20), nullable=False, default='User')
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    device_token = db.Column(db.String(36), unique=True, nullable=True)

class TimeLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id', ondelete='CASCADE'), nullable=False)
    date = db.Column(db.String(50), nullable=False)
    clock_in = db.Column(db.String(50), nullable=False)
    clock_out = db.Column(db.String(50), nullable=True)
    user = db.relationship('User', backref='time_logs')

class TeamSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    value = db.Column(db.String(50), nullable=False)

with app.app_context():
    db.create_all()

# ===============================================================
# == HELPER FUNCTIONS & CONTEXT =================================
# ===============================================================
def get_day_with_suffix(d):
    return f"{d}{'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')}"

# === THIS FUNCTION WAS MISSING - NOW ADDED BACK ===
def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculates distance between two GPS points in meters."""
    R = 6371000  # Earth radius
    lat1_rad, lon1_rad = radians(lat1), radians(lon1)
    lat2_rad, lon2_rad = radians(lat2), radians(lon2)
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = sin(dlat / 2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

@app.before_request
def load_logged_in_user():
    user_id = session.get('user_id')
    g.user = User.query.get(user_id) if user_id else None

def get_team_settings(team_id):
    settings_list = TeamSetting.query.filter_by(team_id=team_id).all()
    settings = {s.name: s.value for s in settings_list}
    settings.setdefault('LocationVerificationEnabled', 'TRUE')
    return settings

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if g.user is None: return redirect(url_for('login'))
        if g.user.role != 'Admin':
            flash("You do not have permission to access this page.", "error")
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def prepare_and_store_action(user):
    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    log_entry = TimeLog.query.filter_by(user_id=user.id, date=today_date, clock_out=None).first()
    action_type = 'Clock Out' if log_entry else 'Clock In'
    session['pending_action'] = {'user_id': user.id, 'action_type': action_type}

# ===============================================================
# == MARKETING AND AUTH ROUTES ==================================
# ===============================================================
@app.route("/")
def home():
    device_token = request.cookies.get('device_token')
    if device_token:
        user = User.query.filter_by(device_token=device_token).first()
        if user:
            # A returning user is recognized, start their workflow
            prepare_and_store_action(user)
            return redirect(url_for('confirm_entry'))
    # New user or cleared cookies, show the storefront
    return render_template("marketing/index.html")

@app.route("/features")
def features(): return render_template("marketing/features.html")
@app.route("/pricing")
def pricing(): return render_template("marketing/pricing.html")
@app.route("/how-to-start")
def how_to_start(): return render_template("marketing/how_to_start.html")

@app.route("/signup", methods=["GET", "POST"])
def admin_signup():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        team_name = request.form.get('team_name')
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists. Please log in.", "error")
            return redirect(url_for('login'))
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        new_team = Team(name=team_name)
        db.session.add(new_team)
        db.session.commit()
        new_admin = User(name=name, email=email, password=hashed_password, role='Admin', team_id=new_team.id)
        db.session.add(new_admin)
        default_setting = TeamSetting(team_id=new_team.id, name='LocationVerificationEnabled', value='TRUE')
        db.session.add(default_setting)
        db.session.commit()
        session['user_id'] = new_admin.id
        flash("Your team and admin account created successfully!", "success")
        return redirect(url_for('admin_dashboard'))
    return render_template("auth/admin_signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and user.password and bcrypt.check_password_hash(user.password, password):
            session['user_id'] = user.id
            return redirect(url_for('admin_dashboard')) if user.role == 'Admin' else redirect(url_for('home')) # Employees don't have a dashboard yet
        else:
            flash("Invalid email or password. Please try again.", "error")
    return render_template("auth/login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been successfully logged out.", "success")
    return redirect(url_for('home'))
# ===============================================================
# == EMPLOYEE WORKFLOW ROUTES ===================================
# ===============================================================
@app.route("/join/<join_token>")
def join_team(join_token):
    team = Team.query.filter_by(join_token=join_token).first_or_404()
    session['join_team_id'] = team.id
    session['join_team_name'] = team.name
    admin = User.query.filter_by(team_id=team.id, role='Admin').first()
    session['join_admin_name'] = admin.name if admin else 'N/A'
    return redirect(url_for('scan'))

@app.route("/scan", methods=["GET", "POST"])
def scan():
    if request.method == 'POST':
        team_id = session.get('join_team_id')
        name = f"{request.form.get('first_name', '').strip()} {request.form.get('last_name', '').strip()}"
        device_token = request.cookies.get('device_token')
        
        user = User.query.filter_by(name=name, team_id=team_id).first()
        if not user:
            user = User(name=name, team_id=team_id, device_token=device_token)
            db.session.add(user)
        else: # User exists, update their device token
            user.device_token = device_token
        db.session.commit()
        
        prepare_and_store_action(user)
        return redirect(url_for('confirm_entry'))

    return render_template("scan.html", team_name=session.get('join_team_name'), admin_name=session.get('join_admin_name'))

@app.route("/handle_typo", methods=["GET", "POST"])
def handle_typo():
    conflict = session.get('typo_conflict')
    if not conflict: return redirect(url_for('scan'))

    if request.method == 'POST':
        choice = request.form.get('choice')
        session.pop('typo_conflict', None)
        if choice == 'yes':
            user = User.query.filter_by(name=conflict['correct_name']).first()
            if user:
                prepare_and_store_action(user)
                return redirect(url_for('confirm_entry'))
        return redirect(url_for('scan'))
        
    return render_template("handle_typo.html", correct_name=conflict['correct_name'])

@app.route("/enable_location")
def enable_location():
    if 'pending_action' not in session: return redirect(url_for('scan'))
    return render_template("enable_location.html")

@app.route("/confirm_entry")
def confirm_entry():
    if 'pending_action' not in session: return redirect(url_for('scan'))
    
    action_data = session['pending_action']
    user = User.query.get(action_data['user_id'])
    settings = get_team_settings(user.team_id)
    location_check_required = settings.get('LocationVerificationEnabled') == 'TRUE'
    
    # This route is the central hub for the workflow
    if location_check_required:
        user_lat_str = request.args.get('lat')
        if not user_lat_str:
            # If location is required but not provided, start the location flow
            return redirect(url_for('enable_location'))
        
        # If location IS provided, verify it
        try:
            building_lat = float(os.environ.get("BUILDING_LATITUDE"))
            building_lon = float(os.environ.get("BUILDING_LONGITUDE"))
            distance = calculate_distance(building_lat, building_lon, float(user_lat_str), float(request.args.get('lon')))
            if (distance * 3.28084) > 500: # Convert meters to feet
                return redirect(url_for('location_failed', message="You are too far away from the building."))
        except (TypeError, ValueError):
            return redirect(url_for('location_failed', message="Could not verify location due to a configuration error."))
    
    return render_template("confirm.html", 
                           action_type=action_data['action_type'], 
                           worker_name=user.name,
                           location_verified=location_check_required)

@app.route("/execute_action", methods=["POST"])
def execute_action():
    if 'pending_action' not in session: return redirect(url_for('scan'))
    
    action_data = session.pop('pending_action')
    user = User.query.get(action_data['user_id'])
    
    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    current_time = now.strftime("%I:%M:%S %p")
    
    if action_data['action_type'] == 'Clock Out':
        log_entry = TimeLog.query.filter_by(user_id=user.id, date=today_date, clock_out=None).first()
        if log_entry: log_entry.clock_out = current_time
    else: # Clock In
        new_log = TimeLog(user_id=user.id, team_id=user.team_id, date=today_date, clock_in=current_time)
        db.session.add(new_log)
    
    db.session.commit()
    return redirect(url_for('success', status=action_data['action_type'].lower().replace(' ', '_'), name=user.name))

@app.route("/success")
def success():
    return render_template("success.html", status_type=request.args.get('status'), worker_name=request.args.get('name'))

@app.route("/location_failed")
def location_failed():
    return render_template("location_failed.html", message=request.args.get('message'))

# ===============================================================
# == ADMIN DASHBOARD SECTION ====================================
# ===============================================================
@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    currently_in = TimeLog.query.join(User).filter(TimeLog.team_id == g.user.team_id, TimeLog.date == today_date, TimeLog.clock_out == None).all()
    user_count = User.query.filter_by(team_id=g.user.team_id).count()
    join_link = url_for('join_team', join_token=g.user.team.join_token, _external=True)
    return render_template("admin/dashboard.html", currently_in=currently_in, join_link=join_link, user_count=user_count)

@app.route("/admin/users")
@admin_required
def admin_users():
    team_users = User.query.filter_by(team_id=g.user.team_id).order_by(User.name).all()
    return render_template("admin/users.html", users=team_users)

@app.route("/admin/profile", methods=["GET", "POST"])
@admin_required
def admin_profile():
    if request.method == 'POST':
        g.user.name = request.form.get('name')
        g.user.email = request.form.get('email')
        g.user.team.name = request.form.get('team_name')
        db.session.commit()
        flash("Profile and team name updated successfully.", "success")
        return redirect(url_for('admin_profile'))
    return render_template("admin/profile.html")

@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == 'POST':
        setting_name = request.form.get("setting_name")
        new_value = "TRUE" if request.form.get("setting_value") == "on" else "FALSE"
        setting = TeamSetting.query.filter_by(team_id=g.user.team_id, name=setting_name).first()
        if setting:
            setting.value = new_value
        else:
            setting = TeamSetting(team_id=g.user.team_id, name=setting_name, value=new_value)
            db.session.add(setting)
        db.session.commit()
        flash(f"Setting '{setting_name}' updated successfully.", "success")
        return redirect(url_for('admin_settings'))
    current_settings = get_team_settings(g.user.team_id)
    return render_template("admin/settings.html", settings=current_settings)

@app.route("/admin/users/set_role/<int:user_id>", methods=["POST"])
@admin_required
def set_user_role(user_id):
    target_user = User.query.filter_by(id=user_id, team_id=g.user.team_id).first_or_404()
    if target_user.id == g.user.id:
        flash("You cannot change your own role.", "error")
        return redirect(url_for('admin_users'))
    new_role = request.form.get('role')
    if new_role in ['Admin', 'User']:
        target_user.role = new_role
        db.session.commit()
        flash(f"{target_user.name}'s role has been updated to {new_role}.", "success")
    return redirect(url_for('admin_users'))

@app.route("/admin/users/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    target_user = User.query.filter_by(id=user_id, team_id=g.user.team_id).first_or_404()
    if target_user.id == g.user.id:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for('admin_users'))
    db.session.delete(target_user)
    db.session.commit()
    flash(f"User {target_user.name} and all their data have been permanently deleted.", "success")
    return redirect(url_for('admin_users'))

@app.route("/admin/api/dashboard_data")
@admin_required
def admin_api_dashboard_data():
    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    currently_in = TimeLog.query.filter(TimeLog.team_id == g.user.team_id, TimeLog.date == today_date, clock_out=None).all()
    data = [{'Name': log.user.name, 'Clock In': log.clock_in, 'id': log.id} for log in currently_in]
    return jsonify(data)
# In app.py, add these three functions to the ADMIN SECTION

@app.route("/admin/time_log")
@admin_required
def admin_time_log():
    # This query now correctly joins the User table to get names
    query = TimeLog.query.join(User).filter(TimeLog.team_id == g.user.team_id)
    
    # Get unique names for the filter dropdown
    all_users_on_team = User.query.filter_by(team_id=g.user.team_id).order_by(User.name).all()
    unique_names = [user.name for user in all_users_on_team]
    
    # Get filter and sort criteria from URL
    filter_name = request.args.get('name', '')
    filter_date = request.args.get('date', '')
    sort_by = request.args.get('sort_by', 'id') # Default sort by log ID (newest first)
    sort_order = request.args.get('sort_order', 'desc')

    if filter_name:
        query = query.filter(User.name == filter_name)
    if filter_date:
        try:
            filter_dt = datetime.strptime(filter_date, "%Y-%m-%d")
            date_str = f"%b. {get_day_with_suffix(filter_dt.day)}, %Y"
            query = query.filter(TimeLog.date == filter_dt.strftime(date_str))
        except ValueError:
            pass # Ignore invalid date format

    # Sorting logic
    sort_column = getattr(TimeLog, sort_by, TimeLog.id)
    if sort_order == 'desc':
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    filtered_logs = query.all()

    return render_template("admin/time_log.html", 
                           logs=filtered_logs, 
                           unique_names=unique_names,
                           filter_name=filter_name,
                           filter_date=filter_date,
                           sort_by=sort_by,
                           sort_order=sort_order)

@app.route("/admin/export_csv")
@admin_required
def export_csv():
    # This function uses the same query logic as the time_log page for consistency
    query = TimeLog.query.join(User).filter(TimeLog.team_id == g.user.team_id)
    filter_name = request.args.get('name', '')
    filter_date = request.args.get('date', '')
    if filter_name:
        query = query.filter(User.name == filter_name)
    if filter_date:
        try:
            filter_dt = datetime.strptime(filter_date, "%Y-%m-%d")
            date_str = f"%b. {get_day_with_suffix(filter_dt.day)}, %Y"
            query = query.filter(TimeLog.date == date_str)
        except ValueError: pass
    
    filtered_logs = query.order_by(TimeLog.id.desc()).all()
    
    # Prepare data for CSV
    logs_for_csv = [{'Name': log.user.name, 'Date': log.date, 'Clock In': log.clock_in, 'Clock Out': log.clock_out} for log in filtered_logs]
    
    output = io.StringIO()
    if logs_for_csv:
        writer = csv.DictWriter(output, fieldnames=['Name', 'Date', 'Clock In', 'Clock Out'])
        writer.writeheader()
        writer.writerows(logs_for_csv)

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = f"attachment; filename=timesheet_export_{datetime.now().strftime('%Y-%m-%d')}.csv"
    response.headers["Content-type"] = "text/csv"
    return response

@app.route("/admin/print_view")
@admin_required
def admin_print_view():
    # This also uses the same query logic
    query = TimeLog.query.join(User).filter(TimeLog.team_id == g.user.team_id)
    filter_name = request.args.get('name', '')
    filter_date = request.args.get('date', '')
    if filter_name:
        query = query.filter(User.name == filter_name)
    if filter_date:
        try:
            filter_dt = datetime.strptime(filter_date, "%Y-%m-%d")
            date_str = f"%b. {get_day_with_suffix(filter_dt.day)}, %Y"
            query = query.filter(TimeLog.date == date_str)
        except ValueError: pass
        
    filtered_logs = query.order_by(TimeLog.id.desc()).all()
    
    generation_time = datetime.now(CENTRAL_TIMEZONE).strftime("%Y-%m-%d %I:%M %p")
    return render_template("admin/print_view.html",
                           logs=filtered_logs,
                           filter_name=filter_name,
                           filter_date=filter_date,
                           generation_time=generation_time)
@app.route("/admin/users/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    # Find the user, but only if they are on the admin's team
    target_user = User.query.filter_by(id=user_id, team_id=g.user.team_id).first_or_404()

    # Security check: an admin cannot delete themselves
    if target_user.id == g.user.id:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for('admin_users'))

    # All associated time logs will be deleted automatically due to 'cascade'
    db.session.delete(target_user)
    db.session.commit()
    flash(f"User {target_user.name} and all their data have been permanently deleted.", "success")
    return redirect(url_for('admin_users'))

@app.route("/admin/users/clear_token/<int:user_id>", methods=["POST"])
@admin_required
def clear_user_token(user_id):
    # Find the user, but only if they are on the admin's team
    target_user = User.query.filter_by(id=user_id, team_id=g.user.team_id).first_or_404()
    
    target_user.device_token = None
    db.session.commit()
    flash(f"Device token for {target_user.name} has been cleared. They can now re-register a new device.", "success")
    return redirect(url_for('admin_users'))

@app.route("/admin/users/set_role/<int:user_id>", methods=["POST"])
@admin_required
def set_user_role(user_id):
    target_user = User.query.filter_by(id=user_id, team_id=g.user.team_id).first_or_404()
    if target_user.id == g.user.id:
        flash("You cannot change your own role.", "error")
        return redirect(url_for('admin_users'))
    
    new_role = request.form.get('role')
    if new_role in ['Admin', 'User']:
        target_user.role = new_role
        db.session.commit()
        flash(f"{target_user.name}'s role has been updated to {new_role}.", "success")
    return redirect(url_for('admin_users'))