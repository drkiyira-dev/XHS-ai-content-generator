from flask import Flask
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from app.config import Config
import os

db = SQLAlchemy()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    CORS(app)
    db.init_app(app)

    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    from app.routes import main_bp
    app.register_blueprint(main_bp)

    return app
