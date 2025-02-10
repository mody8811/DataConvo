import logging
logging.basicConfig(level=logging.DEBUG)

from flask import Flask
from app import create_app  # or however you initialize your app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True) 