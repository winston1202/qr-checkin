from flask import Blueprint, render_template, redirect, url_for, flash, g
from .extensions import db
from .models import Team, User, TeamSetting
from functools import wraps

# A new, separate blueprint for Super Admin functions
super_admin_bp = Blueprint('super_admin', __name__, url_prefix='/super_admin')

# --- Decorator for Super Admin Security ---
def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # g.is_super_admin is set in __init__.py
        if not g.user or not hasattr(g, 'is_super_admin') or not g.is_super_admin:
            flash("You do not have permission to access this page.", "error")
            return redirect(url_for('admin.dashboard')) # Redirect regular admins
        return f(*args, **kwargs)
    return decorated_function

# --- Super Admin Routes ---
@super_admin_bp.route("/")
@super_admin_required
def dashboard():
    """Displays the main Super Admin dashboard with all teams and stats."""
    all_teams = Team.query.order_by(Team.name).all()
    
    # Prepare data with stats for the template
    teams_data = []
    for team in all_teams:
        admin = User.query.filter_by(team_id=team.id, role='Admin').first()
        user_count = User.query.filter_by(team_id=team.id).count()
        settings = {s.name: s.value for s in team.settings}
        teams_data.append({
            'team': team,
            'admin': admin,
            'user_count': user_count,
            'settings': settings
        })

    stats = {
        'total_teams': len(all_teams),
        'total_users': User.query.count()
    }

    return render_template("super_admin/dashboard.html", teams_data=teams_data, stats=stats)

@super_admin_bp.route("/teams/delete/<int:team_id>", methods=["POST"])
@super_admin_required
def delete_team(team_id):
    """Allows the Super Admin to delete an entire team and all its data."""
    team_to_delete = Team.query.get_or_404(team_id)
    
    # The 'cascade' in the database models will automatically delete
    # all users, time logs, and settings associated with this team.
    db.session.delete(team_to_delete)
    db.session.commit()
    
    flash(f"Team '{team_to_delete.name}' and all its data have been permanently deleted.", "success")
    return redirect(url_for('super_admin.dashboard'))