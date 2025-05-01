# import the necessary libraries
from flask import Flask, jsonify, request

import os
import pandas as pd
import geopandas as gpd
from sqlalchemy import create_engine, text
import pyproj
from pyproj import Proj, transform

import logging
from logging.handlers import RotatingFileHandler
import json

app = Flask(__name__)

# Configuration du logging
logger = logging.getLogger('flask_app')
logger.setLevel(logging.DEBUG)

# Formatter pour les messages de log
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Handler pour la console
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Handler pour le fichier (avec rotation pour limiter la taille)
file_handler = RotatingFileHandler('/app/logs/app.log', maxBytes=1000000, backupCount=5)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# the address file for the redis
qc_addresses_file = os.environ.get('DATA_ADDRESS')
# the streets file for neo4j
qc_streets_file = os.environ.get('DATA_STREETS')

# the database url
database_url = os.environ.get('DATABASE_URL')


# function to count the number of addresses and streets in the database
def count_infos_from_db():
    # Connexion à la base de données
    engine = create_engine(database_url)
    count_addresses = 0
    count_streets = 0

    # Vérifier si la table 'addresses' existe
    with engine.connect() as conn:
        table_addresses_exist = conn.execute(text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='addresses')"))
        
        table_streets_exist = conn.execute(text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='streets')"))
        
        # if the table exists, count the number of rows
        if table_addresses_exist.scalar():
            # Compter le nombre d'adresses
            result = conn.execute(text("SELECT COUNT(*) FROM addresses"))
            count_addresses = result.scalar()

        # if the table exists, count the number of rows
        if table_streets_exist.scalar():
            # Compter le nombre de routes    
            result = conn.execute(text("SELECT COUNT(*) FROM streets"))
            count_streets = result.scalar()
        

    logger.debug(f'Counted {count_addresses} addresses and {count_streets} streets in the database')
    return count_addresses, count_streets

# Initialize some example data on startup
def init_addresses():
    # load the addresses file
    gdf = gpd.read_file(qc_addresses_file)
    
    # connect to the database
    engine = create_engine(database_url)

    # import the data into the database
    gdf.to_postgis(
        name="addresses",
        con=engine,
        schema="public",
        if_exists="replace",  # Remplace la table si elle existe
        index=False
    )

    # create a spatial index on the geometry column
    with engine.connect() as conn:
        conn.execute(text("CREATE INDEX addresses_geometry_idx ON addresses USING GIST (geometry);"))
    logger.debug(f'Initialized addresses: {gdf.shape}')

# function to initialize the network
# all roads are in the network (they are bidirectional)
def init_network():
    
    # load the streets file
    gdf = gpd.read_file(qc_streets_file)

    # check if the geometry is valid
    gdf['geometry'] = gdf['geometry'].apply(lambda geom: geom if geom.is_valid else geom.buffer(0))

    # convert the geometry to a single LineString if it is a MultiLineString
    gdf['geometry'] = gdf['geometry'].apply(
        lambda geom: geom.geoms[0] if geom.geom_type == 'MultiLineString' else geom
    )

    # add the columns for the topology (source, target, cost)
    gdf['source'] = pd.Series([None] * len(gdf), dtype='Int32')  # Nullable integer
    gdf['target'] = pd.Series([None] * len(gdf), dtype='Int32')  # Nullable integer
    gdf['cost'] = gdf['geometry'].length  # Coût basé sur la longueur
    
    # connect to the database
    engine = create_engine(database_url)

    # import the data into the database
    gdf.to_postgis(
        name="streets",
        con=engine,
        schema="public",
        if_exists="replace",  # Remplace la table si elle existe
        index=False
    )
    logger.debug(f'Imported streets: {gdf.shape}')
    # create a spatial index
    with engine.connect() as conn:
        # create the topology
        logger.debug("Creating topology...")
        try:
            # use the pgr_createTopology function to create the topology
            result = conn.execute(text("""
                SELECT pgr_createTopology(
                    'streets', 
                    1,
                    'geometry',
                    'OBJECTID',
                    'source',
                    'target'
                );
            """))
            # table -> streets
            # snapping_tolerance -> 1 m (because the data is in meters)
            # id of the edge -> OBJECTID
            conn.commit()  # valid the transaction
            
            logger.debug(f"Topology created: {result.fetchall()}")
        except Exception as e:
            logger.error(f"Error creating topology: {e}")
            return


        # Check if the topology was created successfully
        result = conn.execute(text("""
            SELECT pgr_analyzeGraph(
                'streets',
                1,
                'geometry',
                'OBJECTID',
                'source',
                'target'
            );
        """))
        conn.commit()

        logger.debug(f"Graph analyzed: {result.fetchall()}")
    

# check if the database is empty
cnt_addresses, cnt_streets = count_infos_from_db()

logger.debug(f'Counted {cnt_addresses} addresses and {cnt_streets} streets in the database')
# check if the address file exists
if cnt_addresses == 0:
    init_addresses()
# check if the network exists
if cnt_streets == 0:
    init_network()   

# create the transformer to convert from WGS84 to Quebec Lambert 32187
transformer_4326_32187 = pyproj.Transformer.from_crs(4326, 32187, always_xy=True)
# create the transformer to convert from Quebec Lambert 32187 to WGS84
transformer_32187_4326 = pyproj.Transformer.from_crs(32187, 4326, always_xy=True)

# function to search for addresses in the database
def addressSearch(query):
    
    suggestions = []
    
    # create query on the database to select the address like the text
    
    engine = create_engine(database_url)
    
    with engine.connect() as conn:

        # create the query
        sql_query = text(f"""
            SELECT "ADRESSE" as display_name, ST_Y(geometry) AS latitude, ST_X(geometry) AS longitude
            FROM addresses
            WHERE "ADRESSE" ILIKE :name
        """)
        
        # execute the query
        result = conn.execute(sql_query, {'name': f'%{query}%'})
        
        # fetch all results
        rows = result.fetchall()
        
        # create the suggestions list
        for row in rows:
            suggestions.append({
                "display_name": row[0],
                "lat": row[1],
                "lon": row[2]
            })

    return suggestions

# function to search for nodes in Neo4j
def nodeSearch(latitude, longitude):
    
    locations = []
    x, y = transformer_4326_32187.transform(float(longitude), float(latitude))
    
    engine = create_engine(database_url)
    # find the closest node to the address
    # according the location of the address
    # we will use the <-> operator to find the closest node
    # the <-> operator is KNN search operator
    with engine.connect() as conn:
        # create the query
        query = text("""
            SELECT id, ST_X(the_geom) AS longitude, ST_Y(the_geom) AS latitude
            FROM public.streets_vertices_pgr
            ORDER BY the_geom <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 32187)
            LIMIT 1
        """)
        result = conn.execute(query, {'lon': x, 'lat': y}).fetchone()
        if result:

            lon, lat = transformer_32187_4326.transform(result[1], result[2])

            locations.append({
                'node_id': result[0],
                'longitude': lon,
                'latitude': lat,
                'x': result[1],
                'y': result[2]
            })
        
    
    return locations


# Define the Flask routes
# home route
@app.route('/', methods=['GET'])
def home():
    return {"status": "healthy"}, 200

# route to get the address suggestions
@app.route("/suggest")
def suggest():
    query = request.args.get("q", "").lower()
    if not query or len(query) < 2:
        return jsonify([])

    search_results = addressSearch(query)

    suggestions = [
        {"label": addr['display_name']}
        for addr in search_results
        if query in addr['display_name'].lower()
    ][:10] 

    return jsonify(suggestions)

# route to get the location of the address
@app.route("/location")
def location():
    query = request.args.get("q", "")
    if not query or len(query) < 2:
        return jsonify([])

    # call the addressSearch function
    suggestions = addressSearch(query)

    return jsonify(suggestions)

# route to get the node 
@app.route("/findnode")
def findnode():
    latitude = request.args.get("lat", "")
    longitude = request.args.get("lon", "")
    if not latitude or not longitude:
        return jsonify([])
    # call the nodeSearch function
    locations = nodeSearch(latitude, longitude)

    return jsonify(locations)

# route to get the path between two addresses
@app.route("/findpath")
def findpath():
    # get the start and end addresses from the request
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    if not start or not end :
        return jsonify([])
    # find the closest address to the start and end addresses
    suggest_start = addressSearch(start)
    suggest_end = addressSearch(end)
    
    # we will use the first address found
    if len(suggest_start) > 0:
        first = suggest_start[0]
        # find the closest node to the address    
        suggest_node_start = nodeSearch(first['lat'], first['lon'])
    
    # we will use the first address found
    if len(suggest_end) > 0:
        first = suggest_end[0]
        # find the closest node to the address
        suggest_node_end = nodeSearch(first['lat'], first['lon'])
    
    # we will use the first node found
    if len(suggest_node_start) > 0:
        first_node = suggest_node_start[0]
    # we will use the first node found
    if len(suggest_node_end) > 0:
        second_node = suggest_node_end[0]

        
    logger.debug(f"first_node: {first_node} ")
    logger.debug(f"second_node: {second_node} ")

    objectids = []
    path = []
    length = 0
    geojson_obj = {
        "type": "FeatureCollection",
        "features": []
    }
    # we will use the pgr_dijkstra function to find the shortest path
    # connect to the database
    engine = create_engine(database_url)
    with engine.connect() as conn:
        # create the query
        query = text(f"""
            SELECT d.seq, d.path_seq, d.node, d.edge, d.cost, s.geometry
            FROM pgr_dijkstra(
                'SELECT "OBJECTID" AS id, source, target, cost, cost as reverse_cost FROM streets',
                :start, :end,
                directed => true
            ) AS d
            JOIN streets AS s ON d.edge = s."OBJECTID";
        """)
        # get the path from the database in a geodataframe
        result = gpd.read_postgis(query, conn, geom_col='geometry', params={'start': first_node['node_id'], 'end': second_node['node_id']})

        # reproject the geometry to WGS84
        result.to_crs(epsg=4326, inplace=True)    

    # geojson to display on the map
    geojson_obj = json.loads(result.to_json())
    # the total cost of the path
    length = result['cost'].sum()
    # the object ids of the edges
    objectids = result['edge'].tolist()
    # the node ids of the path
    path = result['node'].tolist()

    return jsonify({"objectids": objectids, "geojson": geojson_obj, "totalCost": length, "nodeNames": path})



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)