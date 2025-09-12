from flask import Blueprint, render_template, request, g, make_response
from .models import User, TimeLog
from .decorators import admin_required
from datetime import datetime
import pytz
import csv
import io

# Define the Blueprint for this section of the app
admin_time_log_bp = Blueprint('admin_time_log', __name__, url_prefix='/admin/time_log')

def get_day_with_suffix(d):
    """Helper function to format dates correctly."""
    return f"{d}{'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')}"

@admin_time_log_bp.route("/")
@admin_required
def time_log():
    """Displays the main Time Clock Log page with filtering and sorting."""
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
        except ValueError: pass

    sort_column = getattr(TimeLog, sort_by, TimeLog.id)
    if sort_order == 'desc':
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())
    
    filtered_logs = query.all()

    return render_template("admin/time_log.html", 
                           logs=filtered_logs, 
                           unique_names=unique_names,
                           filter_name=filter_name,
                           filter_date=filter_date,
                           sort_by=sort_by,
                           sort_order=sort_order)

@admin_time_log_bp.route("/export_csv")
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

@admin_time_log_bp.route("/print_view")
@admin_required
def print_view():
    """Generates a clean, printer-friendly view of the filtered data."""
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