from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from .extensions import db, bcrypt, mail  # <-- CORRECT: Get tools from the central hub
from .models import User, Team, TeamSetting # <-- CORRECT: Get data blueprints from models
from flask_mail import Message
import random
import os

auth_bp = Blueprint('auth', __name__)

@auth_bp.route("/")
def home():
    # --- THIS IS THE FIX ---
    # Check if the user just came from the logout page.
    if request.args.get('logged_out'):
        # If they did, just show the homepage and do nothing else.
        return render_template("marketing/index.html")
    # --- END OF FIX ---

    # If they are a genuine new or returning visitor, run the auto-clock-in logic.
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

@auth_bp.route("/about")
def about_page(): return render_template("marketing/about.html")

@auth_bp.route("/pricing")
def pricing(): return render_template("marketing/pricing.html")

@auth_bp.route("/how-to-start")
def how_to_start(): return render_template("marketing/how_to_start.html")

@auth_bp.route("/help")
def help_page():
    return render_template("marketing/help.html")

# In app/Project/auth.py

@auth_bp.route("/signup", methods=["GET", "POST"])
def admin_signup():
    if request.method == 'POST':
        # Basic form values
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role') or 'Admin'

        # Prevent duplicate emails
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists. Please log in.", "error")
            return redirect(url_for('auth.login'))

        # Employee signup flow (join existing team)
        if role == 'User':
            join_token = request.form.get('join_token')
            if not join_token:
                flash("Please provide a team invitation token to join.", "error")
                return redirect(url_for('auth.admin_signup'))

            team = Team.query.filter_by(join_token=join_token).first()
            if not team:
                flash("Invalid team invitation token.", "error")
                return redirect(url_for('auth.admin_signup'))

            try:
                hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
                new_user = User(
                    name=request.form.get('name'),
                    email=email,
                    password=hashed_password,
                    role='User',
                    team_id=team.id
                )
                db.session.add(new_user)
                db.session.commit()

                # Auto-login newly created employee
                session.clear()
                session['user_id'] = new_user.id
                flash("Your account has been created and you are now logged in.", "success")
                return redirect(url_for('employee.dashboard'))
            except Exception as e:
                current_app.logger.error(f"Failed to create employee account: {e}")
                db.session.rollback()
                flash("Could not create your account. Please try again.", "error")
                return redirect(url_for('auth.admin_signup'))

        # Admin signup flow (create a new team)
        try:
            new_team = Team(name=request.form.get('team_name'))
            db.session.add(new_team)
            db.session.commit()

            hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
            new_admin = User(
                name=request.form.get('name'),
                email=email,
                password=hashed_password,
                role='Admin',
                team_id=new_team.id
            )
            db.session.add(new_admin)
            db.session.commit()

            new_team.owner_id = new_admin.id
            db.session.add(new_team)
            default_setting = TeamSetting(team_id=new_team.id, name='LocationVerificationEnabled', value='FALSE')
            db.session.add(default_setting)
            db.session.commit()

            session.clear()
            session['user_id'] = new_admin.id
            flash("Your team and account have been created successfully!", "success")
            return redirect(url_for('admin.dashboard'))
        except Exception as e:
            current_app.logger.error(f"CRITICAL: Failed to create admin account: {e}")
            db.session.rollback()
            flash("A database error occurred. Could not create your account.", "error")
            return redirect(url_for('auth.admin_signup'))

    return render_template("auth/admin_signup.html")

@auth_bp.route("/verify", methods=["GET", "POST"])
def verify_email():
    if 'temp_signup_data' not in session:
        flash("Your session has expired. Please sign up again.", "error")
        return redirect(url_for('auth.admin_signup'))

    # Define all necessary variables for the template
    signup_data = session.get('temp_signup_data')
    email = signup_data['email']
    form_action_url = url_for('auth.verify_email')
    back_url = url_for('auth.admin_signup')

    if request.method == 'POST':
        submitted_code = request.form.get('code')
        
        if submitted_code == signup_data['code']:
            # Step 1: Create the new team
            new_team = Team(name=signup_data['team_name'])
            db.session.add(new_team)
            db.session.commit()
            
            # Step 2: Create the new admin user
            new_admin = User(
                name=signup_data['name'],
                email=signup_data['email'],
                password=signup_data['hashed_password'],
                role='Admin',
                team_id=new_team.id
            )
            db.session.add(new_admin)
            # We must commit here to assign an ID to the new_admin object
            db.session.commit()
            
            # --- THIS IS THE NEW LOGIC ---
            # Step 3: Now that the admin exists, set them as the owner of the new team
            new_team.owner_id = new_admin.id
            db.session.add(new_team)
            # --- END OF NEW LOGIC ---

            # Step 4: Add default settings for the team
            default_setting = TeamSetting(team_id=new_team.id, name='LocationVerificationEnabled', value='FALSE')
            db.session.add(default_setting)

            # Final commit to save the owner_id and settings
            db.session.commit()

            # Step 5: Log the new user in
            session.clear()
            session['user_id'] = new_admin.id
            flash("Email verified! Your team and account are now active.", "success")
            return redirect(url_for('admin.dashboard'))
        else:
            flash("Incorrect verification code. Please try again.", "error")
            # Re-render the page on failure
            return render_template("auth/verify_email.html", email=email, form_action=form_action_url, back_url=back_url)
            
    # For the GET request, render the verification page
    return render_template("auth/verify_email.html", email=email, form_action=form_action_url, back_url=back_url)

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
# In auth.py
@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been successfully logged out.", "success")
    # --- THIS IS THE FIX ---
    # We add a flag to the URL to signal that a logout just occurred.
    return redirect(url_for('auth.home', logged_out='1'))
    # --- END OF FIX ---

@auth_bp.route("/privacy")
def privacy_policy():
    return render_template("marketing/privacy.html")

@auth_bp.route("/terms")
def terms_of_service():
    return render_template("marketing/terms.html")

