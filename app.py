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

  