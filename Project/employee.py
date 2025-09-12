from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from .models import db, User, Team, TimeLog, TeamSetting
from datetime import datetime
import pytz
from math import radians, sin, cos, sqrt, atan2
import os
import bcrypt

employee_bp = Blueprint('employee', __name__)

# --- Helper Functions ---
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

def get_team_settings(team_id):
    settings_list = TeamSetting.query.filter_by(team_id=team_id).all()
    settings = {s.name: s.value for s in settings_list}
    settings.setdefault('LocationVerificationEnabled', 'TRUE')
    return settings

def prepare_and_store_action(user):
    now = datetime.now(pytz.timezone("America/Chicago"))
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    log_entry = TimeLog.query.filter_by(user_id=user.id, date=today_date, clock_out=None).first()
    already_clocked_out = TimeLog.query.filter(TimeLog.user_id == user.id, TimeLog.date == today_date, TimeLog.clock_out != None).first()
    action_type = 'Clock Out' if log_entry else 'Clock In'
    if already_clocked_out:
        action_type = 'Already Clocked Out'
    session['pending_action'] = {'user_id': user.id, 'action_type': action_type}

# --- Employee Routes ---
@employee_bp.route("/join/<join_token>")
def join_team(join_token):
    team = Team.query.filter_by(join_token=join_token).first_or_404()
    session['join_team_id'] = team.id
    session['join_team_name'] = team.name
    admin = User.query.filter_by(team_id=team.id, role='Admin').first()
    session['join_admin_name'] = admin.name if admin else 'N/A'
    return redirect(url_for('employee.scan'))

@employee_bp.route("/scan", methods=["GET", "POST"])
def scan():
    if request.method == 'POST':
        name = f"{request.form.get('first_name', '').strip()} {request.form.get('last_name', '').strip()}"
        device_token = request.cookies.get('device_token')

        user_by_token = User.query.filter_by(device_token=device_token).first()
        if user_by_token and user_by_token.name.lower() != name.lower():
            session['typo_conflict'] = {'correct_name': user_by_token.name}
            return redirect(url_for('employee.handle_typo'))

        team_id = session.get('join_team_id')
        user_by_name_and_team = User.query.filter_by(name=name, team_id=team_id).first() if team_id else None
        
        if user_by_name_and_team and user_by_name_and_team.device_token and user_by_name_and_team.device_token != device_token:
            flash(f"The name <strong>{name}</strong> is already registered to another device.", "error")
            return redirect(url_for('employee.scan'))
        
        session['new_user_registration'] = {'name': name}
        return redirect(url_for('employee.register'))

    return render_template("scan.html", team_name=session.get('join_team_name'), admin_name=session.get('join_admin_name'))

@employee_bp.route("/register", methods=["GET", "POST"])
def register():
    reg_data = session.get('new_user_registration')
    if not reg_data: return redirect(url_for('employee.scan'))
    if request.method == 'POST':
        choice = request.form.get('choice')
        name = reg_data['name']
        session.pop('new_user_registration', None)
        if choice == 'yes':
            team_id = session.get('join_team_id')
            device_token = request.cookies.get('device_token')
            if not team_id:
                flash("Your session has expired. Please use the invitation link again.", "error")
                return redirect(url_for('auth.home'))
            user = User.query.filter_by(name=name, team_id=team_id).first()
            if not user:
                user = User(name=name, team_id=team_id, device_token=device_token)
                db.session.add(user)
            else:
                user.device_token = device_token
            db.session.commit()
            prepare_and_store_action(user)
            return redirect(url_for('employee.confirm_entry'))
        else:
            return redirect(url_for('employee.scan'))
    return render_template("register.html", new_name=reg_data['name'])

@employee_bp.route("/handle_typo", methods=["GET", "POST"])
def handle_typo():
    conflict = session.get('typo_conflict')
    if not conflict: return redirect(url_for('employee.scan'))
    if request.method == 'POST':
        choice = request.form.get('choice')
        session.pop('typo_conflict', None)
        if choice == 'yes':
            user = User.query.filter_by(name=conflict['correct_name']).first()
            if user:
                prepare_and_store_action(user)
                return redirect(url_for('employee.confirm_entry'))
        return redirect(url_for('employee.scan'))
    return render_template("handle_typo.html", correct_name=conflict['correct_name'])

@employee_bp.route("/enable_location")
def enable_location():
    if 'pending_action' not in session: return redirect(url_for('employee.scan'))
    return render_template("enable_location.html")

@employee_bp.route("/confirm_entry")
def confirm_entry():
    if 'pending_action' not in session: return redirect(url_for('employee.scan'))
    action_data = session['pending_action']
    user = User.query.get(action_data['user_id'])
    if action_data['action_type'] == 'Already Clocked Out':
        return redirect(url_for('employee.success', status='already_complete', name=user.name, user_id=user.id))
    settings = get_team_settings(user.team_id)
    location_check_required = settings.get('LocationVerificationEnabled') == 'TRUE'
    user_lat_str = request.args.get('lat')
    if location_check_required and not user_lat_str:
        return redirect(url_for('employee.enable_location'))
    if location_check_required:
        try:
            building_lat = float(os.environ.get("BUILDING_LATITUDE"))
            building_lon = float(os.environ.get("BUILDING_LONGITUDE"))
            distance = calculate_distance(building_lat, building_lon, float(user_lat_str), float(request.args.get('lon')))
            if (distance * 3.28084) > 500:
                return redirect(url_for('employee.location_failed', message="You are too far away."))
        except (TypeError, ValueError):
            return redirect(url_for('employee.location_failed', message="Configuration error."))
    return render_template("confirm.html", action_type=action_data['action_type'], worker_name=user.name, location_verified=location_check_required)

@employee_bp.route("/execute_action", methods=["POST"])
def execute_action():
    if 'pending_action' not in session: return redirect(url_for('employee.scan'))
    action_data = session.pop('pending_action')
    user = User.query.get(action_data['user_id'])
    now = datetime.now(pytz.timezone("America/Chicago"))
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    current_time = now.strftime("%I:%M:%S %p")
    if action_data['action_type'] == 'Clock Out':
        log_entry = TimeLog.query.filter_by(user_id=user.id, date=today_date, clock_out=None).first()
        if log_entry: log_entry.clock_out = current_time
        db.session.commit()
        return redirect(url_for('employee.success', status='clock_out', name=user.name, user_id=user.id))
    else:
        new_log = TimeLog(user_id=user.id, team_id=user.team_id, date=today_date, clock_in=current_time)
        db.session.add(new_log)
        db.session.commit()
        return redirect(url_for('employee.clocked_in', name=user.name, user_id=user.id))

@employee_bp.route("/clocked_in")
def clocked_in():
    user_id = request.args.get('user_id')
    user = User.query.get(user_id) if user_id else None
    return render_template("employee/clocked_in.html", worker_name=request.args.get('name'), user=user)

@employee_bp.route("/success")
def success():
    return render_template("success.html", status_type=request.args.get('status'), worker_name=request.args.get('name'))

@employee_bp.route("/quick_clock_out", methods=["POST"])
def quick_clock_out():
    user_id = request.form.get("user_id")
    user = User.query.get(user_id) if user_id else None
    if not user: return redirect(url_for('employee.scan'))
    prepare_and_store_action(user)
    return redirect(url_for('employee.confirm_entry'))

@employee_bp.route("/location_failed")
def location_failed():
    return render_template("location_failed.html", message=request.args.get('message'))

@employee_bp.route("/create_account/<int:user_id>", methods=["GET", "POST"])
def create_employee_account(user_id):
    user = User.query.get_or_404(user_id)
    if user.email:
        flash("This user already has a registered account.", "error")
        return redirect(url_for('auth.home'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        if User.query.filter_by(email=email).first():
            flash("That email is already in use. Please choose another.", "error")
            return redirect(url_for('employee.create_employee_account', user_id=user.id))
        user.email = email
        user.password = bcrypt.generate_password_hash(password).decode('utf-8')
        db.session.commit()
        session['user_id'] = user.id
        flash("Your account has been created successfully! You are now logged in.", "success")
        return redirect(url_for('employee.dashboard'))
    return render_template("employee/create_account.html", user=user)