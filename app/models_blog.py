"""BlogPost model for the self-service markdown blog (no AI involved)."""
from app import db
from datetime import datetime


class BlogPost(db.Model):
    """A markdown blog post. Rendered in the browser (marked) — no AI."""
    __tablename__ = 'blog_post'

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(200), unique=True, nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    markdown = db.Column(db.Text, nullable=False)
    author_name = db.Column(db.String(120), nullable=False, default='Data Convo')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'slug': self.slug,
            'title': self.title,
            'markdown': self.markdown,
            'author_name': self.author_name,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }