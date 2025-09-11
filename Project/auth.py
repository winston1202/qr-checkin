
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g
from .models import db, User, Team, TeamSetting
from . import bcrypt

auth_bp = Blueprint('auth', __name__)

@auth_bp.route("/")
def home():
    # Check for device token to redirect returning employees
    device_token = request.cookies.get('device_token')
    if device_token:
        user = User.query.filter_by(device_token=device_token).first()
        if user:
            # Found a returning user, start their workflow
            from .employee import prepare_and_store_action # Import locally to avoid circular import
            prepare_and_store_action(user)
            return redirect(url_for('employee.confirm_entry'))
    # New user or cleared cookies, show the storefront
    return render_template("marketing/index.html")

@auth_bp.route("/features")
def features(): return render_template("marketing/features.html")
@auth_bp.route("/pricing")
def pricing(): return render_template("marketing/pricing.html")
@auth_bp.route("/how-to-start")
def how_to_start(): return render_template("marketing/how_to_start.html")

@auth_bp.route("/signup", methods=["GET", "POST"])
def admin_signup():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        team_name = request.form.get('team_name')
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists. Please log in.", "error")
            return redirect(url_for('auth.login'))
        
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
        return redirect(url_for('admin.dashboard'))
    return render_template("auth/admin_signup.html")

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and user.password and bcrypt.check_password_hash(user.password, password):
            session['user_id'] = user.id
            if user.role == 'Admin':
                return redirect(url_for('admin.dashboard'))
            else:
                # FUTURE: redirect to an employee dashboard
                return redirect(url_for('auth.home'))
        else:
            flash("Invalid email or password. Please try again.", "error")
    return render_template("auth/login.html")

@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("You have been successfully logged out.", "success")
    return redirect(url_for('auth.home'))