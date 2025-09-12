from flask import Flask, g, session
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import os

# Initialize plugins
db = SQLAlchemy()
bcrypt = Bcrypt()

def create_app():
    """Construct the core application."""
    app = Flask(__name__, instance_relative_config=False, template_folder='templates')
    
    # Configure the app
    app.secret_key = os.environ.get("SECRET_KEY")
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Initialize plugins with the app
    db.init_app(app)
    bcrypt.init_app(app)

    with app.app_context():
        # Import models so the app knows about the tables
        from . import models
        
        # This function runs before every request to set up the global user object
        @app.before_request
        def load_logged_in_user():
            user_id = session.get('user_id')
            g.user = models.User.query.get(user_id) if user_id else None

        # Import the Blueprints (the different sections of your app)
        from . import auth
        from . import employee
        from . import admin
        from . import admin_time_log # <-- This is the new, important import

        # Register the Blueprints with the main app
        app.register_blueprint(auth.auth_bp)
        app.register_blueprint(employee.employee_bp)
        app.register_blueprint(admin.admin_bp)
        app.register_blueprint(admin_time_log.admin_time_log_bp) # <-- Register the new Blueprint
        
        # This command creates your database tables if they don't already exist
        db.create_all()

        return app