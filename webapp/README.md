# LeScrape web interface

From the LeScrape repository, install V2's dependencies and Flask. Build the React client once and start the local server.

    python -m pip install -r webapp/requirements.txt
    npm --prefix webapp ci
    npm --prefix webapp run build
    python -m webapp.server

Open http://127.0.0.1:8765. Select the state, district, court complex, establishment (where applicable), case type, filing year, status and limit. Select Run scraper and answer the portal challenge in the monitor. Keep the server running during capture. Each run saves its database, source responses, PDFs and exports in a separate V2/output/web_* directory. The interface uses one worker and a ceiling of one request per second.

# Case storage

The shared SQLite library is `V2/output/case_library.sqlite3`. After a capture finishes, select **Save to DB** in the capture monitor, Saved cases, or Spreadsheet to publish its complete cases there. You can return to a saved run and publish it later. A CNR identifies one current snapshot across runs. The run directory retains its own database, PDFs and exports for historical review. The shared database points to the PDFs in those directories.

Each case write includes the header, parties, hearings, orders, interim applications, acknowledgement and captured source responses in one transaction. A missing linked detail or a failed write leaves the previous complete snapshot available. Failed attempts are recorded in the run database’s `errors` table. Historical run databases can contain partial cases from earlier versions.

Open the shared database in a SQLite client and run the reports in `V2/case_queries.sql`. Both reports use SQL supported by SQLite and PostgreSQL. The storage implementation currently uses SQLite.
