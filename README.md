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

The delivery score is computed when reading or evaluating deliveries based on the business rule:

```
deliveryScore = (signed ? 1.0 : 0.3) × (isMorning ? 1.2 : 1.0)
```

The morning window is 05:00 to 11:00 UTC.

## Decisions

### 1. Consolidated into a single schema

I consolidated the data from both logistics partners into one shared schema and one shared database because both stakeholders need a unified view across partners. Operations wants a cross-partner operations view, and site managers want a cross-partner site view. A single normalized store makes both use cases straightforward and avoids duplicate business logic in separate data stores.

### 2. Separate read endpoints for different consumers

Operations and Construction Site Managers have different query needs, so they have separate GET endpoints:

- `GET /operations/deliveries` for the operations view
- `GET /sites/{destination_code}/deliveries?date=YYYY-MM-DD` for site-specific queries

This keeps each endpoint focused on its own filtering and sorting requirements.

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

## Testing

The repository includes pytest coverage for:

- normalization logic
- fetch behavior and error handling
- deduplication behavior
- pagination validation
- site-level reads
- end-to-end style endpoint flow

Run the tests with:

```bash
docker compose run --rm backend pytest -q
```

## Notes and next steps

The current implementation is intentionally focused on the core integration path and the business rules from the assignment. If this were to move toward production, the next steps would be:

- add proper observability and logging
- add retry and backoff for partner failures
- support incremental or paged partner fetches
- add authentication and authorization
- add schema migrations instead of relying on ad hoc table creation
