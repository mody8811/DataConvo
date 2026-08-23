import sys
import os

# Ensure the project root (where ai_analysis.py resides) is in sys.path.
project_root = os.path.abspath(os.path.dirname(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

def main():
    # If your app.main already defines a main function, it will be used.
    # Otherwise, ensure that app/main.py executes the application properly.
    from app.main import main

    if __name__ == "__main__":
        main()

if __name__ == "__main__":
    main() 