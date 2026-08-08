from datetime import datetime
from app import db


TASK_STATUS_PENDING = "pending"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"


class GenerationRecord(db.Model):
    __tablename__ = "generation_records"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    task_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, default=TASK_STATUS_PENDING, index=True)

    image_path = db.Column(db.String(512), nullable=True)
    image_description = db.Column(db.Text, nullable=True)

    user_input = db.Column(db.Text, nullable=True)

    title = db.Column(db.String(100), nullable=True)
    content = db.Column(db.Text, nullable=True)
    tags = db.Column(db.JSON, nullable=True)

    error_code = db.Column(db.String(64), nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "status": self.status,
            "image_path": self.image_path,
            "image_description": self.image_description,
            "user_input": self.user_input,
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
