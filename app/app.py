from flask import Flask
import psycopg2
from psycopg2 import sql
import os

app = Flask(__name__)

# Database connection
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://geo:geopass123@postgres:5432/gisdb")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

@app.route("/")
def index():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Create a sample table for pgRouting
        cur.execute("""
            CREATE TABLE IF NOT EXISTS roads (
                id SERIAL PRIMARY KEY,
                source INTEGER,
                target INTEGER,
                cost FLOAT,
                geom GEOMETRY(LINESTRING, 4326)
            );
        """)
        
        # Insert sample data
        cur.execute("""
            INSERT INTO roads (source, target, cost, geom)
            VALUES
                (1, 2, 1.0, ST_GeomFromText('LINESTRING(0 0, 1 1)', 4326)),
                (2, 3, 1.5, ST_GeomFromText('LINESTRING(1 1, 2 2)', 4326))
            ON CONFLICT DO NOTHING;
        """)
        
        # Example pgRouting query: shortest path
        cur.execute("""
            SELECT * FROM pgr_dijkstra(
                'SELECT id, source, target, cost FROM roads',
                1, 3
            );
        """)
        path = cur.fetchall()
        
        conn.commit()
        cur.close()
        conn.close()
        
        return f"Shortest path from node 1 to node 3: {path}"
    except Exception as e:
        return f"Error: {str(e)}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)