from flask import Blueprint, render_template, request, redirect, url_for, flash, g, jsonify
from .models import db, User, Team, TimeLog, TeamSetting
from .decorators import admin_required
from datetime import datetime
import pytz

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# --- Helper Functions for This Blueprint ---
def get_day_with_suffix(d):
    return f"{d}{'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')}"

# --- Admin Dashboard Routes ---
@admin_bp.route("/")
@admin_required
def dashboard_redirect():
    return redirect(url_for('admin.dashboard'))

@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    now = datetime.now(pytz.timezone("America/Chicago"))
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    
    currently_in = TimeLog.query.filter(
        TimeLog.team_id == g.user.team_id,
        TimeLog.date == today_date,
        TimeLog.clock_out == None
    ).all()
    
    user_count = User.query.filter_by(team_id=g.user.team_id).count()
    join_link = url_for('employee.join_team', join_token=g.user.team.join_token, _external=True)

    return render_template("admin/dashboard.html", currently_in=currently_in, join_link=join_link, user_count=user_count)

@admin_bp.route("/users")
@admin_required
def users():
    team_users = User.query.filter_by(team_id=g.user.team_id).order_by(User.name).all()
    return render_template("admin/users.html", users=team_users)

@admin_bp.route("/profile", methods=["GET", "POST"])
@admin_required
def profile():
    if request.method == 'POST':
        g.user.name = request.form.get('name')
        g.user.email = request.form.get('email')
        g.user.team.name = request.form.get('team_name')
        db.session.commit()
        flash("Profile and team name updated successfully.", "success")
        return redirect(url_for('admin.profile'))
    return render_template("admin/profile.html")

@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    from .employee import get_team_settings
    
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
        return redirect(url_for('admin.settings'))

    current_settings = get_team_settings(g.user.team_id)
    return render_template("admin/settings.html", settings=current_settings)

# ===============================================================
# == ADMIN ACTION ROUTES (Now Correctly Included) ===============
# ===============================================================
@admin_bp.route("/users/set_role/<int:user_id>", methods=["POST"])
@admin_required
def set_user_role(user_id):
    target_user = User.query.filter_by(id=user_id, team_id=g.user.team_id).first_or_404()
    if target_user.id == g.user.id:
        flash("You cannot change your own role.", "error")
    else:
        new_role = request.form.get('role')
        if new_role in ['Admin', 'User']:
            target_user.role = new_role
            db.session.commit()
            flash(f"{target_user.name}'s role has been updated to {new_role}.", "success")
    return redirect(url_for('admin.users'))

@admin_bp.route("/users/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    target_user = User.query.filter_by(id=user_id, team_id=g.user.team_id).first_or_404()
    if target_user.id == g.user.id:
        flash("You cannot delete your own account.", "error")
    else:
        db.session.delete(target_user)
        db.session.commit()
        flash(f"User {target_user.name} and all their data have been permanently deleted.", "success")
    return redirect(url_for('admin.users'))

@admin_bp.route("/users/clear_token/<int:user_id>", methods=["POST"])
@admin_required
def clear_user_token(user_id):
    target_user = User.query.filter_by(id=user_id, team_id=g.user.team_id).first_or_404()
    target_user.device_token = None
    db.session.commit()
    flash(f"Device token for {target_user.name} has been cleared. They can now re-register a new device.", "success")
    return redirect(url_for('admin.users'))

# --- API Route for Real-Time Dashboard ---
@admin_bp.route("/api/dashboard_data")
@admin_required
def api_dashboard_data():
    now = datetime.now(pytz.timezone("America/Chicago"))
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    
    currently_in = TimeLog.query.filter(
        TimeLog.team_id == g.user.team_id,
        TimeLog.date == today_date,
        TimeLog.clock_out == None
    ).all()
    
    data = [{
        'Name': log.user.name,
        'Clock In': log.clock_in,
        'id': log.id
    } for log in currently_in]
    
    return jsonify(data)