import os
from app import create_app
from whitenoise import WhiteNoise

app = create_app()

static_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app', 'static')
# Specify the prefix '/static/' so that requests to /static/... are correctly mapped.
app.wsgi_app = WhiteNoise(app.wsgi_app, root=static_root, prefix='/static/')

app.config['TEMPLATES_AUTO_RELOAD'] = True

if __name__ == '__main__':
    # Render provides the port via the PORT environment variable.
    port = int(os.environ.get("PORT", 5000))
    # Bind to 0.0.0.0 so that the port is accessible externally.
    app.run(host="0.0.0.0", port=port, debug=True)