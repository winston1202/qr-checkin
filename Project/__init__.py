from flask import Flask, g, session
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_mail import Mail # Added import
import os

# Initialize plugins
db = SQLAlchemy()
bcrypt = Bcrypt()
mail = Mail() # Added mail object

def create_app():
    """Construct the core application."""
    app = Flask(__name__, instance_relative_config=False, template_folder='templates')
    
    # Configure the app
    app.secret_key = os.environ.get("SECRET_KEY")
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Add Mail configuration
    app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER')
    app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
    app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS') == 'True'
    app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
    app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')

    # Initialize plugins with the app
    db.init_app(app)
    bcrypt.init_app(app)
    mail.init_app(app) # Added mail initialization

    with app.app_context():
        from . import models
        
        @app.before_request
        def load_logged_in_user():
            user_id = session.get('user_id')
            g.user = models.User.query.get(user_id) if user_id else None

        # Import and register blueprints
        from . import auth
        from . import employee
        from . import admin
        from . import super_admin
        app.register_blueprint(auth.auth_bp)
        app.register_blueprint(employee.employee_bp)
        app.register_blueprint(admin.admin_bp)
        app.register_blueprint(super_admin.super_admin_bp)
        
        db.create_all()

        return app