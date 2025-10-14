from flask import Blueprint, render_template, request, redirect, url_for, flash, session, make_response, current_app, g
from .extensions import db, bcrypt, mail
from .models import User, Team, TimeLog, TeamSetting, AuditLog
from datetime import datetime
import pytz
from math import radians, sin, cos, sqrt, atan2
import os
from flask_mail import Message
import random
import uuid


employee_bp = Blueprint('employee', __name__)

FREE_TIER_USER_LIMIT = 5

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

@employee_bp.route("/join/<join_token>")
def join_team(join_token):
    device_token = request.cookies.get('device_token')

    # This is the primary check for a returning user on a known device.
    if device_token:
        user = User.query.filter_by(device_token=device_token).first()
        if user:
            # --- THIS IS THE FIX ---
            # We must set the team information in the session here,
            # because the user is skipping the 'scan' page.
            session['join_team_id'] = user.team_id
            session['join_team_name'] = user.team.name
            admin = User.query.filter_by(team_id=user.team_id, role='Admin').first()
            session['join_admin_name'] = admin.name if admin else 'N/A'

            # Now that the session is correctly set up, we can prepare the action.
            prepare_and_store_action(user)
            return redirect(url_for('employee.confirm_entry'))
            # --- END OF FIX ---

    # If the device is not recognized, proceed to the name entry page.
    team = Team.query.filter_by(join_token=join_token).first_or_404()
    session['join_team_id'] = team.id
    session['join_team_name'] = team.name
    admin = User.query.filter_by(team_id=team.id, role='Admin').first()
    session['join_admin_name'] = admin.name if admin else 'N/A'
    
    response = make_response(redirect(url_for('employee.scan')))
    if not device_token:
        new_token = str(uuid.uuid4())
        response.set_cookie('device_token', new_token, max_age=365*24*60*60)
    return response

# In app/Project/employee.py

@employee_bp.route("/scan", methods=["GET", "POST"])
def scan():
    if request.method == 'POST':
        name = f"{request.form.get('first_name', '').strip()} {request.form.get('last_name', '').strip()}"
        device_token = request.cookies.get('device_token')
        team_id = session.get('join_team_id')

        if not team_id:
            flash("You must use a valid invitation link.", "error")
            return redirect(url_for('auth.home'))

        # Find the user by the name they entered.
        user_by_name = User.query.filter_by(name=name, team_id=team_id).first()

        if not user_by_name:
            # The user does not exist on this team. They are brand new.
            session['new_user_registration'] = {'name': name}
            return redirect(url_for('employee.register'))

        # --- THE FINAL, CORRECT LOGIC ---
        # The user exists. Now, let's figure out the security.

        # If the user is a "Floating User", they bypass the strict token checks.
        if user_by_name.is_floating:
            user_by_name.device_token = device_token # Update token for convenience
            db.session.commit()
            prepare_and_store_action(user_by_name)
            return redirect(url_for('employee.confirm_entry'))

        # For NORMAL users, we enforce strict security.
        # Check if this device is already registered to a different user.
        user_by_token = User.query.filter_by(device_token=device_token).first()
        if user_by_token and user_by_token.id != user_by_name.id:
            session['typo_conflict'] = {'correct_name': user_by_token.name}
            return redirect(url_for('employee.handle_typo'))
        
        # Check if this user is already locked to a different device.
        if user_by_name.device_token and user_by_name.device_token != device_token:
            flash(f"<strong>Security Alert:</strong> This name is registered to a different device. Please ask an admin to click 'Clear Token' for {name}.", "error")
            return redirect(url_for('employee.scan'))
        
        # All checks passed. This is a secure login.
        user_by_name.device_token = device_token
        db.session.commit()
        prepare_and_store_action(user_by_name)
        return redirect(url_for('employee.confirm_entry'))

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
            if not team_id:
                flash("Your session has expired. Please use the invitation link again.", "error")
                return redirect(url_for('auth.home'))

            team = Team.query.get(team_id)
            
            # --- THIS IS THE CORRECTED LOGIC ---
            # This query now ONLY counts users with the role 'User', ignoring Admins.
            current_employee_count = User.query.filter_by(team_id=team_id, role='User').count()
            
            if team.plan == 'Free' and current_employee_count >= FREE_TIER_USER_LIMIT:
                # I also improved the error message to be more specific.
                flash(f"The employee limit of {FREE_TIER_USER_LIMIT} for the Free plan has been reached. Please upgrade to the Pro plan to add more users.", "error")
                return redirect(url_for('employee.scan'))
            # --- END OF CORRECTED LOGIC ---

            device_token = request.cookies.get('device_token')
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

@employee_bp.route("/handle_typo")
def handle_typo():
    """
    Displays a security alert page when a device is already registered
    to a different user name than the one provided.
    """
    # Get the conflicting name from the session. If it's not there, just redirect.
    conflict = session.get('typo_conflict')
    if not conflict:
        return redirect(url_for('employee.scan'))
    
    # We've shown the message, so we can clear the session data now.
    correct_name = session.pop('typo_conflict', {}).get('correct_name', 'another user')
    
    # Render the informational alert page.
    return render_template("handle_typo.html", correct_name=correct_name)

@employee_bp.route("/enable_location")
def enable_location():
    if 'pending_action' not in session: return redirect(url_for('employee.scan'))
    return render_template("enable_location.html")

@employee_bp.route("/confirm_entry")
def confirm_entry():
    if 'pending_action' not in session: 
        return redirect(url_for('employee.scan'))
        
    action_data = session['pending_action']
    user = User.query.get(action_data['user_id'])

    # --- THIS IS THE FIX ---
    # The redirect for an "already clocked out" user was still using the old, incorrect endpoint name.
    if action_data['action_type'] == 'Already Clocked Out':
        return redirect(url_for(
            'employee.employee_success', # <-- This has been corrected
            status='already_complete', 
            name=user.name, 
            user_id=user.id
        ))
    # --- END OF FIX ---
    
    settings = get_team_settings(user.team_id)
    location_check_required = settings.get('LocationVerificationEnabled') == 'TRUE'
    user_lat_str = request.args.get('lat')
    
    if location_check_required and not user_lat_str:
        return redirect(url_for('employee.enable_location'))
        
    if location_check_required:
        try:
            building_lat = float(settings.get('BuildingLatitude') or os.environ.get("BUILDING_LATITUDE"))
            building_lon = float(settings.get('BuildingLongitude') or os.environ.get("BUILDING_LONGITUDE"))
            allowed_radius_feet = int(settings.get('GeofenceRadiusFeet') or 500)
            distance = calculate_distance(building_lat, building_lon, float(user_lat_str), float(request.args.get('lon')))
            
            if (distance * 3.28084) > allowed_radius_feet:
                log_detail = f"Clock-in failed. User was {int(distance * 3.28084)} feet from the geofence center."
                log_entry = AuditLog(team_id=user.team_id, user_id=user.id, event_type="Geofence Failure", details=log_detail)
                db.session.add(log_entry)
                db.session.commit()
                return redirect(url_for('employee.location_failed', message=f"You are too far away. You must be within {allowed_radius_feet} feet."))
        except (TypeError, ValueError, AttributeError):
            return redirect(url_for('employee.location_failed', message="Could not verify location due to a configuration error."))
            
    return render_template("confirm.html", action_type=action_data['action_type'], worker_name=user.name, location_verified=location_check_required)

@employee_bp.route("/execute_action", methods=["POST"])
def execute_action():
    if 'pending_action' not in session: 
        return redirect(url_for('employee.scan'))
        
    action_data = session.pop('pending_action')
    user = User.query.get(action_data['user_id'])

    if not user:
        flash("This user no longer exists in the system. The action was cancelled.", "error")
        return redirect(url_for('auth.home'))

    now = datetime.now(pytz.timezone("America/Chicago"))
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    current_time = now.strftime("%I:%M:%S %p")
    status_type = ''

    if action_data['action_type'] == 'Clock Out':
        log_entry = TimeLog.query.filter_by(user_id=user.id, date=today_date, clock_out=None).first()
        if log_entry: 
            log_entry.clock_out = current_time
        status_type = 'clock_out'
    else:
        new_log = TimeLog(user_id=user.id, team_id=user.team_id, date=today_date, clock_in=current_time)
        db.session.add(new_log)
        status_type = 'clock_in'
        
    db.session.commit()
    
    # --- THIS IS THE FIX ---
    # It now points to the new, unique endpoint name: 'employee_success'
    return redirect(url_for(
        'employee.employee_success',  # <-- Renamed for clarity
        status=status_type, 
        name=user.name, 
        user_id=user.id
    ))
    
# In app/Project/employee.py

@employee_bp.route("/quick_clock_out", methods=["POST"])
def quick_clock_out():
    # User ID is taken from g.user if logged in, or form data if from success page
    user = g.user
    if not user:
        user_id = request.form.get("user_id")
        user = User.query.get(user_id) if user_id else None
    
    if not user:
        return redirect(url_for('auth.home'))
        
    prepare_and_store_action(user)
    
    # --- THIS IS THE CHANGE ---
    # Check if we should redirect back to the dashboard
    next_url = request.form.get("next")
    if next_url:
        # Perform the action immediately and redirect back
        return execute_action() 
    # --- END OF CHANGE ---

    return redirect(url_for('employee.confirm_entry'))

@employee_bp.route("/location_failed")
def location_failed():
    return render_template("location_failed.html", message=request.args.get('message'))

# In app/Project/employee.py

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

        # --- THIS IS THE NEW, SIMPLIFIED LOGIC ---
        # We are skipping the email verification and creating the account directly.
        
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        
        user.email = email
        user.password = hashed_password
        db.session.commit()

        # Log the user in directly.
        session['user_id'] = user.id
        flash("Your account has been created successfully! You are now logged in.", "success")
        return redirect(url_for('employee.dashboard'))
        # --- END OF NEW LOGIC ---

    return render_template("employee/create_account.html", user=user)

# In app/Project/employee.py

@employee_bp.route("/dashboard")
def dashboard():
    user_id = session.get('user_id')
    if not user_id:
        flash("You must be logged in to view your dashboard.", "error")
        return redirect(url_for('auth.login'))
    
    user = g.user

    # --- THIS IS THE FIX ---
    # The 'from . import ...' line has been REMOVED.
    # The function can now correctly find the local helper function.
    now = datetime.now(pytz.timezone("America/Chicago"))
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    # --- END OF FIX ---

    todays_log = TimeLog.query.filter_by(user_id=user.id, date=today_date).first()
    
    current_status = 'not_clocked_in'
    if todays_log:
        if todays_log.clock_out is None:
            current_status = 'clocked_in'
        else:
            current_status = 'complete'

    my_logs = TimeLog.query.filter_by(user_id=user.id).order_by(TimeLog.id.desc()).all()
    
    return render_template("employee/dashboard.html", 
                           logs=my_logs, 
                           current_status=current_status,
                           current_log=todays_log,
                           today_date=today_date)

@employee_bp.route("/verify_email", methods=["GET", "POST"])
def verify_employee_email():
    if 'temp_employee_account_data' not in session:
        flash("Your session has expired. Please try creating your account again.", "error")
        return redirect(url_for('auth.home'))

    # Define all necessary variables once at the beginning
    account_data = session.get('temp_employee_account_data')
    email = account_data['email']
    user_id = account_data['user_id']
    form_action_url = url_for('employee.verify_employee_email')
    
    # --- THIS IS THE NEW LOGIC ---
    # Define the correct "back" URL for the employee flow, which needs the user_id
    back_url = url_for('employee.create_employee_account', user_id=user_id)
    # --- END OF NEW LOGIC ---

    if request.method == 'POST':
        submitted_code = request.form.get('code')
        # account_data is already fetched above

        if submitted_code == account_data['code']:
            user_to_update = User.query.get(account_data['user_id'])
            if user_to_update:
                user_to_update.email = account_data['email']
                user_to_update.password = account_data['hashed_password']
                db.session.commit()

                session.pop('temp_employee_account_data', None)
                session['user_id'] = user_to_update.id
                flash("Email verified! Your account is now active and you are logged in.", "success")
                return redirect(url_for('employee.dashboard'))
            else:
                flash("Could not find user record. Please contact support.", "error")
                return redirect(url_for('auth.home'))
        else:
            flash("Incorrect verification code. Please try again.", "error")
            # Re-render the page on failure, now passing the back_url
            return render_template("auth/verify_email.html", email=email, form_action=form_action_url, back_url=back_url)

    # For the GET request, pass the new back_url variable to the template
    return render_template("auth/verify_email.html", email=email, form_action=form_action_url, back_url=back_url)

@employee_bp.route("/clock_in_success")
def employee_success():
# --- END OF FIX ---
    user_id = request.args.get('user_id')
    status_type = request.args.get('status')
    worker_name = request.args.get('name')

    user = User.query.get(user_id) if user_id else None

    if not all([user, status_type, worker_name]):
        flash("An unexpected error occurred while showing the success page. Please check your dashboard to confirm your status.", "error")
        return redirect(url_for('auth.home'))

    return render_template("success.html", 
                           status_type=status_type, 
                           worker_name=worker_name, 
                           user=user)