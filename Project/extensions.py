# app/Project/extensions.py

from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_mail import Mail
from flask_session import Session

db = SQLAlchemy()
bcrypt = Bcrypt()
mail = Mail()
sess = Session()