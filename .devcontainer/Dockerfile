FROM python:3.11-slim

WORKDIR /workspaces/gis_starter_pgrouting_geolab

COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app .

CMD ["flask", "run", "--host=0.0.0.0", "--port=5000"]