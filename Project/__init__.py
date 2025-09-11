from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import os

# Initialize database and bcrypt objects
db = SQLAlchemy()
bcrypt = Bcrypt()

def create_app():
    """Construct the core application."""
    app = Flask(__name__, instance_relative_config=False)
    
    # Application Configuration
    app.secret_key = os.environ.get("SECRET_KEY")
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Initialize Plugins
    db.init_app(app)
    bcrypt.init_app(app)

    with app.app_context():
        # Import parts of our application
        from . import models  # Import models so SQLAlchemy knows about them
        from . import auth
        from . import employee
        from . import admin

        # Register Blueprints
        app.register_blueprint(auth.auth_bp)
        app.register_blueprint(employee.employee_bp)
        app.register_blueprint(admin.admin_bp)
        
        # Create database tables for our models
        db.create_all()

        return app