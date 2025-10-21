import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

def test_connection():
    dsn = os.getenv("DB_URL")
    conn = psycopg2.connect(dsn)
    conn.close()
    print("Database connection successful")

if __name__ == "__main__":
    test_connection()
