# ===============================================================
# == IMPORTS AND SETUP ==========================================
# ===============================================================
from flask import Flask, request, redirect, render_template, session, url_for, flash, g
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import uuid
from datetime import datetime
import pytz
import os
import time
from functools import wraps

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
# == DATABASE MODELS ============================================
# ===============================================================
class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    join_token = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    users = db.relationship('User', backref='team', lazy=True, cascade="all, delete-orphan")

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password = db.Column(db.String(60), nullable=True)
    role = db.Column(db.String(20), nullable=False, default='User') # 'User' or 'Admin'
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    phone_number = db.Column(db.String(20), nullable=True)

class TimeLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id', ondelete='CASCADE'), nullable=False)
    date = db.Column(db.String(50), nullable=False)
    clock_in = db.Column(db.String(50), nullable=False)
    clock_out = db.Column(db.String(50), nullable=True)
    user = db.relationship('User', backref='time_logs')

with app.app_context():
    db.create_all()

# ===============================================================
# == HELPER FUNCTIONS & CONTEXT =================================
# ===============================================================
def get_day_with_suffix(d):
    return f"{d}{'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')}"

@app.before_request
def load_logged_in_user():
    user_id = session.get('user_id')
    g.user = User.query.get(user_id) if user_id else None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if g.user is None: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if g.user is None: return redirect(url_for('login'))
        if g.user.role != 'Admin':
            flash("You do not have permission to access this page.", "error")
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

# ===============================================================
# == MARKETING AND AUTHENTICATION ROUTES ========================
# ===============================================================
@app.route("/")
def home():
    return render_template("marketing/index.html")

@app.route("/features")
def features():
    return render_template("marketing/features.html")

@app.route("/pricing")
def pricing():
    return render_template("marketing/pricing.html")

@app.route("/how-to-start")
def how_to_start():
    return render_template("marketing/how_to_start.html")

# In app.py, replace the entire admin_signup function with this one.

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
        
        # === THIS IS THE FIX: We create and save the team FIRST ===
        # 1. Create the Team object.
        new_team = Team(name=team_name)
        # 2. Add it to the session and commit it to the database.
        db.session.add(new_team)
        db.session.commit()
        # 3. Now, the 'new_team' object has a permanent ID (e.g., new_team.id)

        # 4. NOW we can create the User and link it to the new team's ID.
        new_admin = User(name=name, email=email, password=hashed_password, role='Admin', team_id=new_team.id)
        db.session.add(new_admin)
        db.session.commit()

        # Log the new admin in and proceed.
        session['user_id'] = new_admin.id
        flash("Your team and admin account have been created successfully!", "success")
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
            if user.role == 'Admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('employee_dashboard'))
        else:
            flash("Invalid email or password. Please try again.", "error")
            return redirect(url_for('login'))
    return render_template("auth/login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been successfully logged out.", "success")
    return redirect(url_for('home'))

# ===============================================================
# == EMPLOYEE-FACING ROUTES =====================================
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
    team_name = session.get('join_team_name')
    admin_name = session.get('join_admin_name')
    if request.method == 'POST':
        team_id = session.get('join_team_id')
        if not team_id:
            flash("Invalid or expired invitation link. Please use a valid link.", "error")
            return redirect(url_for('home'))
        
        name = f"{request.form.get('first_name', '').strip()} {request.form.get('last_name', '').strip()}"
        if not request.form.get('first_name') or not request.form.get('last_name'):
             flash("First and last name are required.", "error")
             return render_template("scan.html", team_name=team_name, admin_name=admin_name)

        user = User.query.filter_by(name=name, team_id=team_id).first()
        if not user:
            user = User(name=name, team_id=team_id)
            db.session.add(user)
            db.session.commit()
        
        now = datetime.now(CENTRAL_TIMEZONE)
        today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
        current_time = now.strftime("%I:%M:%S %p")
        
        new_log = TimeLog(user_id=user.id, team_id=team_id, date=today_date, clock_in=current_time)
        db.session.add(new_log)
        db.session.commit()
        
        flash(f"Welcome, {name}! You've been successfully clocked in.", "success")
        return redirect(url_for('home'))

    return render_template("scan.html", team_name=team_name, admin_name=admin_name)

# ===============================================================
# == ACCOUNT & DASHBOARD ROUTES (Admin & Employee) ==============
# ===============================================================
@app.route("/admin")
@admin_required
def admin_redirect():
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    now = datetime.now(CENTRAL_TIMEZONE)
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    
    currently_in = TimeLog.query.filter(
        TimeLog.team_id == g.user.team_id,
        TimeLog.date == today_date,
        TimeLog.clock_out == None
    ).all()
    
    join_link = url_for('join_team', join_token=g.user.team.join_token, _external=True)

    return render_template("admin/dashboard.html", currently_in=currently_in, join_link=join_link)

@app.route("/admin/users")
@admin_required
def admin_users():
    team_users = User.query.filter_by(team_id=g.user.team_id).order_by(User.name).all()
    return render_template("admin/users.html", users=team_users)

@app.route("/admin/profile", methods=["GET", "POST"])
@admin_required
def admin_profile():
    if request.method == 'POST':
        # Ensure user can only edit their own profile and team
        g.user.name = request.form.get('name')
        g.user.email = request.form.get('email')
        g.user.team.name = request.form.get('team_name')
        db.session.commit()
        flash("Profile and team name updated successfully.", "success")
        return redirect(url_for('admin_profile'))
    return render_template("admin/profile.html")

@app.route("/employee/dashboard")
@login_required
def employee_dashboard():
    my_logs = TimeLog.query.filter_by(user_id=g.user.id).order_by(TimeLog.id.desc()).all()
    return render_template("employee/dashboard.html", logs=my_logs)

# NOTE: Super Admin routes have been REMOVED as per the final plan.