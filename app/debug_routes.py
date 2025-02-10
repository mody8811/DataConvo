from flask import Blueprint, jsonify
import pyodbc

debug_bp = Blueprint('debug', __name__)

@debug_bp.route('/debug-drivers', methods=['GET'])
def debug_drivers():
    # Return a JSON list of available ODBC drivers
    drivers = pyodbc.drivers()
    return jsonify({"available_drivers": drivers})