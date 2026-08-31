"""Self-service blog: read public, paste markdown to publish (no AI)."""
from flask import Blueprint, render_template, request, redirect, url_for, abort
from flask_login import login_required, current_user
import re

from app import db
from app.models_blog import BlogPost

blog = Blueprint('blog', __name__)


def _slugify(title):
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    return slug or 'post'


@blog.route('/blog')
def list_posts():
    posts = BlogPost.query.order_by(BlogPost.created_at.desc()).all()
    return render_template('blog_list.html', posts=posts)


@blog.route('/blog/new', methods=['GET', 'POST'])
@login_required
def new_post():
    if current_user.role != 'admin':
        return render_template('403.html', title='Access Restricted',
                               reason='Only admins can publish blog posts.'), 403
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        markdown = request.form.get('markdown') or ''
        if not title or not markdown.strip():
            return render_template('blog_new.html',
                                   error='Title and content are required.',
                                   title_value=title, markdown=markdown)
        base = _slugify(title)
        slug = base
        n = 2
        while BlogPost.query.filter_by(slug=slug).first():
            slug = f'{base}-{n}'
            n += 1
        post = BlogPost(slug=slug, title=title, markdown=markdown,
                        author_name=current_user.username or 'Data Convo')
        db.session.add(post)
        db.session.commit()
        return redirect(url_for('blog.view_post', slug=slug))
    return render_template('blog_new.html')


@blog.route('/blog/<slug>')
def view_post(slug):
    post = BlogPost.query.filter_by(slug=slug).first_or_404()
    return render_template('blog_detail.html', post=post)