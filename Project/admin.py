# app/Project/admin.py

from flask import Blueprint, render_template, request, g, make_response, redirect, url_for, flash, jsonify
from .extensions import db
from .models import User, Team, TimeLog, TeamSetting, AuditLog
from .decorators import admin_required
from datetime import datetime
import pytz
import csv
import io
import os
from sqlalchemy import or_
import qrcode
import base64

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def get_day_with_suffix(d):
    """Helper function to format dates correctly."""
    return f"{d}{'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')}"

@admin_bp.route("/")
@admin_required
def dashboard_redirect():
    """Redirects /admin to /admin/dashboard for a cleaner URL."""
    return redirect(url_for('admin.dashboard'))

@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    """Displays the main admin dashboard."""
    now = datetime.now(pytz.timezone("America/Chicago"))
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    
    currently_in = TimeLog.query.filter(
        TimeLog.team_id == g.user.team_id,
        TimeLog.date == today_date,
        TimeLog.clock_out == None
    ).all()
    
    super_admin_email = os.environ.get('SUPER_ADMIN_USERNAME')
    user_count = User.query.filter(
        User.team_id == g.user.team_id,
        or_(User.email != super_admin_email, User.email == None)
    ).count()

    join_link = url_for('employee.join_team', join_token=g.user.team.join_token, _external=True)

    return render_template("admin/dashboard.html", currently_in=currently_in, join_link=join_link, user_count=user_count)

@admin_bp.route("/time_log")
@admin_required
def time_log():
    """Displays the filterable and sortable Time Clock Log page."""
    query = TimeLog.query.join(User).filter(TimeLog.team_id == g.user.team_id)
    
    all_users_on_team = User.query.filter_by(team_id=g.user.team_id).order_by(User.name).all()
    unique_names = [user.name for user in all_users_on_team]
    
    filter_name = request.args.get('name', '')
    filter_date = request.args.get('date', '')
    sort_by = request.args.get('sort_by', 'id')
    sort_order = request.args.get('sort_order', 'desc')

    if filter_name:
        query = query.filter(User.name == filter_name)
    if filter_date:
        try:
            filter_dt = datetime.strptime(filter_date, "%Y-%m-%d")
            date_str = f"%b. {get_day_with_suffix(filter_dt.day)}, %Y"
            query = query.filter(TimeLog.date == date_str)
        except ValueError:
            pass

    sort_column = getattr(TimeLog, sort_by, TimeLog.id)
    if sort_order == 'desc':
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())
    
    filtered_logs = query.all()

    return render_template(
        "admin/time_log.html", 
        logs=filtered_logs, 
        unique_names=unique_names,
        filter_name=filter_name,
        filter_date=filter_date,
        sort_by=sort_by,
        sort_order=sort_order
    )

@admin_bp.route("/users")
@admin_required
def users():
    """Displays the user management page."""
    super_admin_email = os.environ.get('SUPER_ADMIN_USERNAME')
    team_users = User.query.filter(
        User.team_id == g.user.team_id,
        or_(User.email != super_admin_email, User.email == None)
    ).order_by(User.role.desc(), User.name).all()
    
    return render_template("admin/users.html", users=team_users)

@admin_bp.route("/profile", methods=["GET", "POST"])
@admin_required
def profile():
    """Handles admin and team profile updates."""
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
        lat = request.form.get("latitude")
        lon = request.form.get("longitude")
        enabled = "TRUE" if request.form.get("location_enabled") == "on" else "FALSE"
        radius = request.form.get("radius_feet")

        settings_map = {
            'LocationVerificationEnabled': enabled,
            'BuildingLatitude': lat,
            'BuildingLongitude': lon,
            'GeofenceRadiusFeet': radius
        }

        for name, value in settings_map.items():
            setting = TeamSetting.query.filter_by(team_id=g.user.team_id, name=name).first()
            if setting:
                setting.value = value
            else:
                setting = TeamSetting(team_id=g.user.team_id, name=name, value=value)
                db.session.add(setting)
        
        db.session.commit()
        flash("Settings updated successfully.", "success")
        return redirect(url_for('admin.settings'))

    current_settings = get_team_settings(g.user.team_id)
    return render_template("admin/settings.html", settings=current_settings)

@admin_bp.route("/export_csv")
@admin_required
def export_csv():
    """Generates and downloads a CSV file based on the current filters."""
    query = TimeLog.query.join(User).filter(TimeLog.team_id == g.user.team_id)
    filter_name = request.args.get('name', '')
    filter_date = request.args.get('date', '')
    if filter_name: query = query.filter(User.name == filter_name)
    if filter_date:
        try:
            filter_dt = datetime.strptime(filter_date, "%Y-%m-%d")
            date_str = f"%b. {get_day_with_suffix(filter_dt.day)}, %Y"
            query = query.filter(TimeLog.date == date_str)
        except ValueError: pass
    
    filtered_logs = query.order_by(TimeLog.id.desc()).all()
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

@admin_bp.route("/print_view")
@admin_required
def print_view():
    """Generates a clean, printer-friendly view of the filtered data."""
    # This function appears to be duplicated in your original file. I have removed the duplicate.
    query = TimeLog.query.join(User).filter(TimeLog.team_id == g.user.team_id)
    filter_name = request.args.get('name', '')
    filter_date = request.args.get('date', '')
    if filter_name: query = query.filter(User.name == filter_name)
    if filter_date:
        try:
            filter_dt = datetime.strptime(filter_date, "%Y-%m-%d")
            date_str = f"%b. {get_day_with_suffix(filter_dt.day)}, %Y"
            query = query.filter(TimeLog.date == date_str)
        except ValueError: pass
        
    filtered_logs = query.order_by(TimeLog.id.desc()).all()
    
    generation_time = datetime.now(pytz.timezone("America/Chicago")).strftime("%Y-%m-%d %I:%M %p")
    return render_template("admin/print_view.html",
                           logs=filtered_logs,
                           filter_name=filter_name,
                           filter_date=filter_date,
                           generation_time=generation_time)

@admin_bp.route("/users/set_role/<int:user_id>", methods=["POST"])
@admin_required
def set_user_role(user_id):
    target_user = User.query.filter_by(id=user_id, team_id=g.user.team_id).first_or_404()
    
    if target_user.id == g.user.id:
        flash("You cannot change your own role.", "error")
        return redirect(url_for('admin.users'))

    new_role = request.form.get('role')
    
    if new_role == 'Admin' and g.user.team.plan == 'Free':
        flash("The Free plan is limited to 1 Admin per team. Please upgrade to Pro to add more admins.", "error")
        return redirect(url_for('admin.users'))

    if not target_user.email:
        flash(f"Cannot promote {target_user.name}. They must create an account with an email and password first.", "error")
        return redirect(url_for('admin.users'))

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

@admin_bp.route("/api/dashboard_data")
@admin_required
def api_dashboard_data():
    now = datetime.now(pytz.timezone("America/Chicago"))
    today_date = now.strftime(f"%b. {get_day_with_suffix(now.day)}, %Y")
    currently_in = TimeLog.query.filter(TimeLog.team_id == g.user.team_id, TimeLog.date == today_date, TimeLog.clock_out == None).all()
    data = [{'Name': log.user.name, 'Clock In': log.clock_in, 'id': log.id} for log in currently_in]
    return jsonify(data)

@admin_bp.route("/fix_clock_out/<int:log_id>", methods=["POST"])
@admin_required
def fix_clock_out(log_id):
    log_entry = TimeLog.query.filter_by(id=log_id, team_id=g.user.team_id).first()
    if log_entry:
        log_entry.clock_out = datetime.now(pytz.timezone("America/Chicago")).strftime("%I:%M:%S %p")
        db.session.commit()
    return redirect(url_for('admin.dashboard'))

@admin_bp.route("/time_log/delete/<int:log_id>", methods=["POST"])
@admin_required
def delete_time_log(log_id):
    log_entry = TimeLog.query.filter_by(id=log_id, team_id=g.user.team_id).first_or_404()
    db.session.delete(log_entry)
    db.session.commit()
    flash("Time log entry has been successfully deleted.", "success")
    return redirect(url_for('admin.time_log'))

@admin_bp.route("/audit_log")
@admin_required
def audit_log():
    logs = AuditLog.query.filter_by(team_id=g.user.team_id).order_by(AuditLog.timestamp.desc()).all()
    return render_template("admin/audit_log.html", logs=logs)

@admin_bp.route("/generate_qr_code")
@admin_required
def generate_qr_code():
    join_link = url_for('employee.join_team', join_token=g.user.team.join_token, _external=True)
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(join_link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_str = base64.b64encode(buf.getvalue()).decode("utf-8")
    return jsonify({"qr_code_image": img_str})

@admin_bp.route("/print_qr_code") # No methods needed, defaults to GET
@admin_required
def print_qr_code():
    """
    Generates a QR code from the team's join link AND renders the
    branded, printer-friendly page all in one step.
    """
    # 1. Build the join link
    join_link = url_for('employee.join_team', join_token=g.user.team.join_token, _external=True)

    # 2. Generate the QR code image in memory
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(join_link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    # 3. Save the image to a byte buffer
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    
    # 4. Encode the image data in base64 to pass to the template
    qr_code_image_src = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('utf-8')}"

    # 5. Render the beautiful print template with the image data
    return render_template("admin/print_qr.html", qr_code_image_src=qr_code_image_src)

@admin_bp.route("/users/toggle_floating/<int:user_id>", methods=["POST"])
@admin_required
def toggle_floating_user(user_id):
    """Toggles the 'is_floating' status for a user."""
    target_user = User.query.get_or_404(user_id)
    
    # Security check
    if target_user.team_id != g.user.team_id:
        return "Unauthorized", 403

    # Toggle the boolean status
    target_user.is_floating = not target_user.is_floating
    db.session.commit()
    
    status = "enabled" if target_user.is_floating else "disabled"
    flash(f"Floating user mode has been {status} for {target_user.name}. This is for users with unreliable browsers.", "success")
    return redirect(url_for('admin.users'))