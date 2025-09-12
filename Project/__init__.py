from flask import Flask, g, session
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import os

db = SQLAlchemy()
bcrypt = Bcrypt()

def create_app():
    """Construct the core application."""
    app = Flask(__name__, instance_relative_config=False, template_folder='templates')
    
    app.secret_key = os.environ.get("SECRET_KEY")
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)
    bcrypt.init_app(app)

    with app.app_context():
        from . import models
        
        @app.before_request
        def load_logged_in_user():
            user_id = session.get('user_id')
            g.user = models.User.query.get(user_id) if user_id else None

        # Import the Blueprints
        from . import auth
        from . import employee
        from . import admin # <-- We only need to import this one now

        # Register the Blueprints
        app.register_blueprint(auth.auth_bp)
        app.register_blueprint(employee.employee_bp)
        app.register_blueprint(admin.admin_bp) # <-- And register this one
        
        db.create_all()

        return app