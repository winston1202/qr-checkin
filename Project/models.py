# app/Project/models.py

from .extensions import db  # <-- THE FIX: Import from the new central file
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
    users = db.relationship('User', backref='team', lazy=True, cascade="all, delete-orphan")
    settings = db.relationship('TeamSetting', backref='team', lazy=True, cascade="all, delete-orphan")
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

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