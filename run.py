import os
from app import create_app

app = create_app()
app.config['TEMPLATES_AUTO_RELOAD'] = True

if __name__ == '__main__':
    # Render provides the port via the PORT environment variable.
    port = int(os.environ.get("PORT", 5000))
    # Bind to 0.0.0.0 so that the port is accessible externally.
    app.run(host="0.0.0.0", port=port, debug=True)