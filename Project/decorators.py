from functools import wraps
from flask import g, redirect, url_for, flash

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # If no user is logged in, send them to the login page.
        if g.user is None:
            return redirect(url_for('auth.login'))
        
        # If the user is not an Admin, send them to the home page with an error.
        if g.user.role != 'Admin':
            flash("You do not have permission to access this page.", "error")
            return redirect(url_for('auth.home'))
            
        # If they are an admin, proceed with the original function.
        return f(*args, **kwargs)
    return decorated_function