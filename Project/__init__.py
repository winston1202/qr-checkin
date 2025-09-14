from flask import Flask, g, session
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_mail import Mail
from flask_session import Session  # <-- NEW IMPORT
import os
import stripe

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

    # --- NEW: CONFIGURE SERVER-SIDE SESSIONS ---
    app.config['SESSION_TYPE'] = 'sqlalchemy'  # Use your database to store sessions
    app.config['SESSION_PERMANENT'] = True      # Make sessions permanent
    app.config['SESSION_USE_SIGNER'] = True     # Sign the session cookie for security
    app.config['SESSION_SQLALCHEMY'] = db       # Use the existing db connection
    # --- END OF NEW CONFIGURATION ---

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
    sess.init_app(app)  # <-- INITIALIZE THE NEW SESSION MANAGER

    with app.app_context():
        from . import models
        
        @app.before_request
        def load_logged_in_user():
            user_id = session.get('user_id')
            g.user = models.User.query.get(user_id) if user_id else None
            
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

        return app