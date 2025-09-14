from Project import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
# This code goes at the end of app/app.py

import os
import click
from Project.models import db, User, Team
from Project import bcrypt

@app.cli.command("create-super-admin")
@click.argument("name")
@click.argument("email")
@click.argument("password")
def create_super_admin(name, email, password):
    """Creates the Super Admin user."""
    # Check if the super admin email from environment variables is set
    super_admin_env_email = os.environ.get('SUPER_ADMIN_USERNAME')
    if not super_admin_env_email or email != super_admin_env_email:
        print(f"Error: The provided email '{email}' does not match the SUPER_ADMIN_USERNAME environment variable.")
        return

    # Check if user already exists
    if User.query.filter_by(email=email).first():
        print(f"User with email {email} already exists.")
        return

    # Find or create a special team for system users
    system_team = Team.query.filter_by(name="System Administration").first()
    if not system_team:
        system_team = Team(name="System Administration")
        db.session.add(system_team)
        db.session.commit()
        print("Created the 'System Administration' team.")

    # Create the super admin user
    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
    new_super_admin = User(
        name=name,
        email=email,
        password=hashed_password,
        role='Admin',  # Super admin still has the 'Admin' role for access
        team_id=system_team.id
    )
    db.session.add(new_super_admin)
    db.session.commit()
    print(f"Super Admin '{name}' created successfully.")

# ======================================================================
# TEMPORARY CODE TO CREATE SUPER ADMIN - DELETE AFTER USE
# ======================================================================
@app.route("/setup-initial-admin-and-delete-this-route")
def setup_initial_admin():
    """
    One-time use endpoint to create the Super Admin on the live server.
    DELETE THIS ENTIRE FUNCTION AFTER YOU HAVE USED IT ONCE.
    """
    from Project.models import db, User, Team
    from Project import bcrypt
    import os

    # --- Use the exact details for your admin ---
    SUPER_ADMIN_NAME = "Winston Choate"
    SUPER_ADMIN_EMAIL = "winstonandreagan@gmail.com"
    SUPER_ADMIN_PASSWORD = "Wrc1234"

    # Check if the user already exists to prevent running this twice
    if User.query.filter_by(email=SUPER_ADMIN_EMAIL).first():
        return "<h1>Admin user already exists. You can now delete this code from app.py.</h1>"

    try:
        # Find or create the special "System Administration" team
        system_team = Team.query.filter_by(name="System Administration").first()
        if not system_team:
            system_team = Team(name="System Administration")
            db.session.add(system_team)
            db.session.commit()

        # Create the super admin user
        hashed_password = bcrypt.generate_password_hash(SUPER_ADMIN_PASSWORD).decode('utf-8')
        new_super_admin = User(
            name=SUPER_ADMIN_NAME,
            email=SUPER_ADMIN_EMAIL,
            password=hashed_password,
            role='Admin',
            team_id=system_team.id
        )
        db.session.add(new_super_admin)
        db.session.commit()
        
        return "<h1>Super Admin created successfully! You can now log in. PLEASE DELETE THIS CODE FROM app.py AND REDEPLOY.</h1>"
    
    except Exception as e:
        return f"<h1>An error occurred:</h1><p>{str(e)}</p>"
# ======================================================================
# END OF TEMPORARY CODE
# ======================================================================