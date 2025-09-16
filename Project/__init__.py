from flask import Flask, g, session, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_mail import Mail
from flask_session import Session  # <-- NEW IMPORT
import os
import stripe
from datetime import datetime, timezone
from .models import db 

# Initialize plugins
db = SQLAlchemy()
bcrypt = Bcrypt()
mail = Mail()
sess = Session()  # <-- NEW SESSION OBJECT

def create_app():
    """Construct the core application."""
    app = Flask(__name__, instance_relative_config=False, template_folder='templates')
    
    
    # Configure the app
    app.secret_key = os.environ.get("SECRET_KEY")
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # --- NEW, MORE ROBUST SESSION CONFIGURATION ---
    app.config['SESSION_TYPE'] = 'sqlalchemy'
    app.config['SESSION_PERMANENT'] = True
    app.config['SESSION_USE_SIGNER'] = True
    app.config['SESSION_SQLALCHEMY'] = db
    
    # These settings are crucial for cookies to persist across external redirects in production.
    app.config['SESSION_COOKIE_SECURE'] = True  # Ensures the cookie is only sent over HTTPS
    app.config['SESSION_COOKIE_SAMESITE'] = 'None' # Allows the cookie to be sent on cross-site requests
    app.config['SESSION_COOKIE_HTTPONLY'] = True # Prevents client-side JS from accessing the cookie

    # Add Mail configuration
    app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER')
    app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
    app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS') == 'True'
    app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
    app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')

    # Configure Stripe
    stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

    # Initialize plugins with the app
    db.init_app(app)
    bcrypt.init_app(app)
    mail.init_app(app)
    sess.init_app(app)

    with app.app_context():
        from . import models
        
        @app.before_request
        def load_logged_in_user():
            user_id = session.get('user_id')
            g.user = models.User.query.get(user_id) if user_id else None

            @app.context_processor
            def inject_now():
            # Use the modern, timezone-aware method for UTC
                return {'now': datetime.now(timezone.utc)}
            
            if g.user and g.user.email:
                super_admin_email = os.environ.get('SUPER_ADMIN_USERNAME')
                g.is_super_admin = (g.user.email == super_admin_email)
            else:
                g.is_super_admin = False

        # Import and register blueprints
        from . import auth, employee, admin, super_admin, payments
        app.register_blueprint(auth.auth_bp)
        app.register_blueprint(employee.employee_bp)
        app.register_blueprint(admin.admin_bp)
        app.register_blueprint(super_admin.super_admin_bp)
        app.register_blueprint(payments.payments_bp)
        
        db.create_all()

        

        @app.errorhandler(404)
        def page_not_found(e):
            return render_template('404.html'), 404

        @app.errorhandler(500)
        def internal_server_error(e):
            # You might want to add error logging here in the future
            return render_template('500.html'), 500

        return app
    
    