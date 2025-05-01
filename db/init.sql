-- Create a new user 
CREATE USER qc_user WITH PASSWORD 'qc_password';

-- Create a new database
CREATE DATABASE qc_routing;

-- Grant all privileges on the database to the user
GRANT ALL PRIVILEGES ON DATABASE qc_routing TO qc_user;

-- Connect to the new database
\connect qc_routing

-- Create the PostGIS extension
-- and the pgrouting extension
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

-- Create the public schema
CREATE SCHEMA IF NOT EXISTS public;


-- Grant all privileges on the public schema to the user
GRANT ALL ON SCHEMA public TO qc_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT ALL ON TABLES TO qc_user;

-- Change the owner of the tables to the user
ALTER TABLE public.spatial_ref_sys OWNER TO qc_user;
ALTER TABLE public.geometry_columns OWNER TO qc_user;
ALTER TABLE public.geography_columns OWNER TO qc_user;

-- sql cmd to connect to the database
-- psql -U qc_user -W -d qc_routing