CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, telegram_id BIGINT UNIQUE, first_name VARCHAR(128), locale VARCHAR(8) DEFAULT 'zh');
CREATE TABLE IF NOT EXISTS orders (id BIGSERIAL PRIMARY KEY, customer_id BIGINT, driver_id BIGINT, status VARCHAR(32) DEFAULT 'DISPATCHING', pickup_lng NUMERIC, pickup_lat NUMERIC, dropoff_lng NUMERIC, dropoff_lat NUMERIC, distance_km NUMERIC, price_estimate NUMERIC, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS settings (key VARCHAR(64) PRIMARY KEY, value NUMERIC);
INSERT INTO settings (key, value) VALUES ('base_fare', 1.0) ON CONFLICT DO NOTHING;
INSERT INTO settings (key, value) VALUES ('per_km_rate', 0.5) ON CONFLICT DO NOTHING;
