# VESTIGAS Backend Challenge

## Architecture

The backend is a small FastAPI service that acts as a bridge between two logistics partners and the downstream consumers inside VESTIGAS.

### Flow

1. Operations triggers a refresh by calling the fetch endpoint.
2. The service calls both partner APIs independently using `asyncio.gather`.
3. Each partner payload is normalized into a single canonical delivery shape.
4. The normalized data is upserted into a shared Postgres table so both operations and site managers can read from one unified store.
5. Read endpoints expose the data in the two shapes required by the stakeholders.

### Components

- FastAPI app in [backend/main.py](backend/main.py)
  - handles HTTP endpoints
  - normalizes partner payloads
  - writes to Postgres via `asyncpg`
- Postgres database from [docker-compose.yml](docker-compose.yml)
  - stores the unified deliveries table
- Mock logistics services
  - [mock_logistics_a](mock_logistics_a)
  - [mock_logistics_b](mock_logistics_b)
  - simulate the partner APIs the service must integrate with

### Data model

The service consolidates both partners into one schema:

- `deliveryId`
- `supplier`
- `timestamp`
- `status`
- `signed`
- `signee`
- `destinationCode`
- `address`

The delivery score is computed when reading or evaluating deliveries.
I chose not to persist a deliveryScore column. It is calculated in the query from signed and timestamp, because the score is fully derived from stored normalized fields. That avoids stale derived data if a delivery is updated by a later fetch, keeps the stored model simpler, and still lets operations sort by score directly in SQL.

## Decisions

### 1. Consolidated into a single schema

I consolidated the data from both logistics partners into one shared schema and one shared database because both stakeholders need a unified view across partners. Operations wants a cross-partner operations view, and site managers want a cross-partner site view. A single normalized store makes both use cases straightforward and avoids duplicate business logic in separate data stores.

### 2. Separate read endpoints for different consumers

Operations and Construction Site Managers have different query needs, so they have separate GET endpoints:

- `GET /operations/deliveries` for the operations view
- `GET /sites/{destination_code}/deliveries?date=YYYY-MM-DD` for site-specific queries

This keeps each endpoint focused on its own filtering and sorting requirements.
Both endpoints support pagination with limit and offset. The limit is intentionally left at 200 to demonstrate that all of the data is being fetched.

### 3. Fetch endpoint uses POST

I implemented the refresh action as `POST /operations/fetch` even though it does not create a resource in the classic REST sense. The endpoint is still a write-triggering action because it causes data to be fetched from external services and upserted into the local database. Using POST also makes it explicit that this is an action that mutates state and avoids data duplication through idempotent upserts.

### 4. No eager prefetch on startup

I chose not to prefetch data during app startup. The fetch operation is intentionally on-demand so Operations remains in full control of when fresh data is pulled from the partners.

### 5. Partner unreliability handling

Partner failures are handled independently so one partner being unavailable does not block the other:

- Partner A and Partner B are fetched independently via `asyncio.gather`.
- Each partner fetch catches its own `httpx.HTTPError` and payload-shape errors.
- If one partner fails, the other can still fetch and store successfully.
- The fetch response reports per-partner outcome and overall status:
  - `ok` if both succeed
  - `partial` if one fails
  - `failed` if both fail

## Endpoints

### Fetch deliveries

- `POST /operations/fetch`
- Fetches data from both mock logistics services
- Normalizes and stores it in the shared database

### Operations view

- `GET /operations/deliveries`
- Returns the unified view of deliveries ordered by delivery score descending

### Site view

- `GET /sites/{destination_code}/deliveries?date=YYYY-MM-DD`
- Returns deliveries for a single site on a specific UTC date

## Running the project

From the repository root:

```bash
docker compose up --build
```

The backend service is exposed through the Traefik route prefix `/backend` in the Docker setup.

## Accesssing the endpoints locally:

Operations GET:
http://localhost:8000/backend/operations/deliveries 

Operations POST:
http://localhost:8000/backend/operations/fetch

Construction site managers GET (example):
http://localhost:8000/backend/sites/munich-schwabing-2/deliveries?date=2025-08-01


## Testing

The repository includes pytest coverage for:

- normalization logic
- fetch behavior and error handling including database read APIs
- malformed partner record skipping
- deduplication behavior
- pagination validation
- site-level reads
- end-to-end style endpoint flow (integration-style test)

Run the tests with:

```bash
docker compose run --rm backend pytest -q
```

## Assumptions

### Fetch load doubling

The guard that prevents a second fetch within two minutes is currently stored in Python memory on the running backend process. That means:

- if the backend container restarts, the two-minute memory is lost and a new fetch could be triggered immediately;
- if the backend were run with multiple workers or multiple replicas, each instance would have its own guard, so duplicate partner calls could still happen.

For this challenge and the single-container scaffold, that is acceptable. For a stronger implementation, I would store fetch attempts in Postgres, for example in a `fetch_runs` table with a `startedAt` timestamp, and enforce the guard inside a database transaction before calling partners.

### Partner unreliability limitation

The current implementation is still synchronous: the caller waits while the backend calls both partners. That is acceptable for the challenge and the current mock APIs. In production, this could evolve into a persisted background fetch job so the API remains responsive during slow or flaky partner calls.

### Pagination

Both read endpoints support `limit` and `offset`.

What I did not add:

- partner filters, because stakeholders explicitly do not care which partner a record came from;
- status, supplier, or date-range filters for the operations view, because the brief only requires a single prioritized view;
- arbitrary sort parameters, because operations already defined the default priority sort.

### Malformed partner records

Malformed individual records are skipped, logged as warnings, and the rest of the fetch continues. The fetch response surfaces a `skipped` count per partner so consumers can see that some rows were not stored. Certain malformations could potentially be adjusted during the fetch if the stakeholders provide information on how the malformed data should be interpreted and transformed.

This choice avoids failing the entire run because of one bad record while still preserving visibility into the issue.

### Fresh enough

The current interpretation of “fresh enough” is “fresh after a successful manual fetch.”

`POST /operations/fetch` is synchronous:

1. Operations calls it.
2. The backend calls both partners.
3. The backend stores the data.
4. The response returns counts and errors when the run is complete.

That is reasonable for the mock APIs and the challenge scope. A production version might use asynchronous fetch jobs if partner APIs become slower or more complex.

### Mocks return everything at once

The current implementation assumes each partner returns one full list per request. That matches the current mocks. I treated pagination and incremental sync as future work because the mock partners do not expose cursors, pages, timestamps, or changed-since filters.

The storage model can already handle future incremental updates because it upserts by `deliveryId`, so new rows can be inserted and existing rows updated. The fetch loop itself would need to evolve once real partners expose pagination or delta endpoints.

### Domain interpretation gaps

Some domain questions remain unresolved and were left to future domain experts. Below two such examples:

1. The partners use different status terminology (for example one uses `OK` and another uses `Delivered`). It is not yet clear whether those should be treated as equivalent values or kept distinct in normalized data.
2. The brief does not define how to interpret ambiguous partner statuses such as one partner reporting `failed` and another reporting `cancelled`. That decision should be made with domain experts before the normalization rules are finalized.

## What's next

The current implementation is intentionally focused on the core integration path and the business rules from the assignment. If this were to move toward production, the next steps would be:

- clear up data model and data intepretation ambiguity with domain experts for more advanced malformation handling
- turn the synchronous fetch request into a persisted fetch job so the caller does not have to wait for the partners
-  store fetch attempts in Postgres, for example in a `fetch_runs` table with a `startedAt` timestamp, and enforce the guard inside a database transaction before calling partners to avoid load doubling with multiple replicas and across multiple runs
- add pagination to the fetch requests if the partner APIs expose cursors or pages in the future
- use async fetch jobs if pagination support is added
- add proper observability and logging
- add retry and backoff for partner failures
- support incremental or paged partner fetches
- add authentication and authorization
- add schema migrations instead of relying on ad hoc table creation
- add a real integration test that brings up containers including one for the database
