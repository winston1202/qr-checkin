from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from .models import db, User, Team, TeamSetting
from . import bcrypt, mail # Import mail object
from flask_mail import Message # Import Message object
import random
import os

auth_bp = Blueprint('auth', __name__)

@auth_bp.route("/")
def home():
    # Check for device token to redirect returning employees
    device_token = request.cookies.get('device_token')
    if device_token:
        user = User.query.filter_by(device_token=device_token).first()
        if user:
            from .employee import prepare_and_store_action
            prepare_and_store_action(user)
            return redirect(url_for('employee.confirm_entry'))
    return render_template("marketing/index.html")

@auth_bp.route("/features")
def features(): return render_template("marketing/features.html")

@auth_bp.route("/pricing")
def pricing(): return render_template("marketing/pricing.html")

@auth_bp.route("/how-to-start")
def how_to_start(): return render_template("marketing/how_to_start.html")

@auth_bp.route("/help")
def help_page():
    return render_template("marketing/help.html")

@auth_bp.route("/signup", methods=["GET", "POST"])
def admin_signup():
    if request.method == 'POST':
        email = request.form.get('email')
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists. Please log in.", "error")
            return redirect(url_for('auth.login'))
        
        password = request.form.get('password')
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        verification_code = f"{random.randint(100000, 999999)}"
        
        session['temp_signup_data'] = {
            'name': request.form.get('name'),
            'email': email,
            'hashed_password': hashed_password,
            'team_name': request.form.get('team_name'),
            'code': verification_code
        }

        try:
            msg = Message("Your TimeClock Verification Code", recipients=[email])
            msg.body = f"Your verification code is: {verification_code}"
            mail.send(msg)
            flash("A verification code has been sent to your email.", "success")
            return redirect(url_for('auth.verify_email'))
        except Exception as e:
            flash(f"Could not send email. Check server configuration. Error: {e}", "error")
            return redirect(url_for('auth.admin_signup'))

    return render_template("auth/admin_signup.html")

@auth_bp.route("/verify", methods=["GET", "POST"])
def verify_email():
    if 'temp_signup_data' not in session:
        flash("Your session has expired. Please sign up again.", "error")
        return redirect(url_for('auth.admin_signup'))

    # Define these once for both GET and POST
    email = session['temp_signup_data']['email']
    form_action_url = url_for('auth.verify_email')

    if request.method == 'POST':
        submitted_code = request.form.get('code')
        signup_data = session.get('temp_signup_data')

        if submitted_code == signup_data['code']:
            new_team = Team(name=signup_data['team_name'])
            db.session.add(new_team)
            db.session.commit()
            
            new_admin = User(
                name=signup_data['name'], 
                # ... (other user details) ...
                team_id=new_team.id
            )
            db.session.add(new_admin)
            
            # --- THIS IS THE LINE TO CHANGE ---
            default_setting = TeamSetting(team_id=new_team.id, name='LocationVerificationEnabled', value='FALSE')
            # --- END OF CHANGE ---

            db.session.add(default_setting)
            db.session.commit()

            session.clear()
            session['user_id'] = new_admin.id
            flash("Email verified! Your team and account are now active.", "success")
            return redirect(url_for('admin.dashboard'))
        else:
            flash("Incorrect verification code. Please try again.", "error")
            # Re-render the page on failure instead of redirecting
            return render_template("auth/verify_email.html", email=email, form_action=form_action_url)
            
    # For the GET request
    return render_template("auth/verify_email.html", email=email, form_action=form_action_url)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()

        if user and user.password and bcrypt.check_password_hash(user.password, password):
            session['user_id'] = user.id

            # --- NEW: Super Admin Check ---
            # Get the Super Admin email from environment variables
            super_admin_email = os.environ.get('SUPER_ADMIN_USERNAME')

            # If the logged-in user is the Super Admin, redirect them immediately
            if user.email == super_admin_email:
                return redirect(url_for('super_admin.dashboard'))
            # --- END NEW ---

            # Otherwise, proceed with the normal redirect logic
            if user.role == 'Admin':
                return redirect(url_for('admin.dashboard'))
            else:
                return redirect(url_for('employee.dashboard'))
        else:
            flash("Invalid email or password. Please try again.", "error")
            
    return render_template("auth/login.html")
@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been successfully logged out.", "success")
    return redirect(url_for('auth.home'))