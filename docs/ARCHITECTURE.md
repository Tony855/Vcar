# Vcar target architecture

## Decisions

- Keep the existing Python/FastAPI and Aiogram application during migration.
- Refactor toward a modular monolith; do not split every domain into microservices.
- PostgreSQL is the source of truth for users, orders, payments, and audit history.
- Redis is a cache and real-time location/dispatch index, not the authoritative order store.
- OSRM is an internal routing engine behind a Vcar RoutingService; it is not a map tile server.
- Leaflet/OpenStreetMap rendering and routing are separate concerns.
- Telegram Bot handlers and Web/API endpoints must call the same application services.
- Production migration is gated by backup/restore rehearsal and compatibility tests.

## Routing topology

frontend -> Vcar API -> RoutingService -> private OSRM :5000

The first regional extract is Cambodia from Geofabrik. Data download and OSRM preprocessing are explicit operator actions; no large map file is downloaded during application deployment. Routing binds to loopback by default.

## Migration sequence

1. Security and dependency baseline.
2. Domain services and repositories.
3. Alembic schema history and legacy data compatibility.
4. OSRM adapter and route/table/match/nearest endpoints.
5. Redis-backed location stream and ETA dispatch.
6. Passenger/driver/admin experience.
7. Production deployment, monitoring, restore drills, and staged cutover.

## First milestone non-goals

- No production database migration.
- No changes to existing systemd services.
- No automatic public map-tile scraping.
- No claim of live traffic routing; vanilla OSRM uses the prepared road graph and profile speeds.
