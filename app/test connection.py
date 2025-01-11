import pyodbc

# Define the connection string
conn_str = (
    "DRIVER={SQL Server};"
    "SERVER=CGGANADBS01Q,16205;"
    "DATABASE=SAP;"
    "UID=modyahm;"
    "PWD=Jana&rayan_2299;"
    "Trusted_connection=True"
)

try:
    conn = pyodbc.connect(conn_str)
    print("Connection successful")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
