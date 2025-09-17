# app/Project/models.py

from .extensions import db
import uuid
from datetime import datetime
import pytz

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    join_token = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    plan = db.Column(db.String(50), nullable=False, default='Free')
    stripe_customer_id = db.Column(db.String(100), nullable=True, unique=True)
    pro_access_expires_at = db.Column(db.DateTime, nullable=True)
    
    # --- THIS IS THE FIX ---
    # We now explicitly tell SQLAlchemy which foreign key is for the "owner"
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    # --- END OF FIX ---

    # We tell the 'users' relationship to use the User.team_id foreign key
    users = db.relationship('User', foreign_keys='User.team_id', backref='team', lazy=True, cascade="all, delete-orphan")
    settings = db.relationship('TeamSetting', backref='team', lazy=True, cascade="all, delete-orphan")

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password = db.Column(db.String(60), nullable=True)
    role = db.Column(db.String(20), nullable=False, default='User')
    
    # --- THIS IS THE FIX ---
    # We now explicitly tell SQLAlchemy which foreign key is for the general "team member"
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    # --- END OF FIX ---
    
    device_token = db.Column(db.String(36), unique=True, nullable=True)
    show_upgrade_success = db.Column(db.Boolean, default=False)
    
    # This relationship links back to the "owner_id" on the Team model
    owned_team = db.relationship('Team', foreign_keys=[Team.owner_id], backref='owner', uselist=False)

# ... (TimeLog, TeamSetting, and AuditLog classes remain unchanged) ...
class TimeLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id', ondelete='CASCADE'), nullable=False)
    date = db.Column(db.String(50), nullable=False)
    clock_in = db.Column(db.String(50), nullable=False)
    clock_out = db.Column(db.String(50), nullable=True)
    user = db.relationship('User', backref=db.backref('time_logs', cascade="all, delete-orphan"))

class TeamSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    value = db.Column(db.String(50), nullable=False)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    event_type = db.Column(db.String(100), nullable=False)
    details = db.Column(db.String(255), nullable=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(pytz.timezone("America/Chicago")))
    user = db.relationship('User')