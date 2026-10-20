--
-- PostgreSQL database dump
--

\restrict p71RJfgwdubd34URyMdRMBbqw1gF3YV2hHwvcv8erCTcUmWq0P4bQqbtIUJhVCn

-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: orders; Type: TABLE; Schema: public; Owner: vcar
--

CREATE TABLE public.orders (
    id bigint NOT NULL,
    customer_id bigint,
    driver_id bigint,
    status character varying(32) DEFAULT 'DISPATCHING'::character varying,
    pickup_lng numeric,
    pickup_lat numeric,
    dropoff_lng numeric,
    dropoff_lat numeric,
    distance_km numeric,
    price_estimate numeric,
    pricing_type character varying(16) DEFAULT 'FIXED'::character varying,
    cancel_fee numeric DEFAULT 0,
    cancel_by character varying(32)
);


ALTER TABLE public.orders OWNER TO vcar;

--
-- Name: orders_id_seq; Type: SEQUENCE; Schema: public; Owner: vcar
--

CREATE SEQUENCE public.orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.orders_id_seq OWNER TO vcar;

--
-- Name: orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: vcar
--

ALTER SEQUENCE public.orders_id_seq OWNED BY public.orders.id;


--
-- Name: reviews; Type: TABLE; Schema: public; Owner: vcar
--

CREATE TABLE public.reviews (
    id integer NOT NULL,
    order_id bigint,
    driver_id bigint,
    passenger_id bigint,
    rating integer,
    comment text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT reviews_rating_check CHECK (((rating >= 1) AND (rating <= 5)))
);


ALTER TABLE public.reviews OWNER TO vcar;

--
-- Name: reviews_id_seq; Type: SEQUENCE; Schema: public; Owner: vcar
--

CREATE SEQUENCE public.reviews_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.reviews_id_seq OWNER TO vcar;

--
-- Name: reviews_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: vcar
--

ALTER SEQUENCE public.reviews_id_seq OWNED BY public.reviews.id;


--
-- Name: settings; Type: TABLE; Schema: public; Owner: vcar
--

CREATE TABLE public.settings (
    key character varying(64) NOT NULL,
    value character varying(255)
);


ALTER TABLE public.settings OWNER TO vcar;

--
-- Name: users; Type: TABLE; Schema: public; Owner: vcar
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    telegram_id bigint,
    first_name character varying(128),
    locale character varying(8) DEFAULT 'zh'::character varying,
    driver_status character varying(32) DEFAULT 'NONE'::character varying,
    username character varying(64),
    phone character varying(32),
    plate_number character varying(32),
    car_photo character varying(128),
    license_photo character varying(128),
    driving_license_photo character varying(128),
    updated_at timestamp with time zone DEFAULT now(),
    rating numeric DEFAULT 5.0,
    rating_count integer DEFAULT 1
);


ALTER TABLE public.users OWNER TO vcar;

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: vcar
--

CREATE SEQUENCE public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO vcar;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: vcar
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: orders id; Type: DEFAULT; Schema: public; Owner: vcar
--

ALTER TABLE ONLY public.orders ALTER COLUMN id SET DEFAULT nextval('public.orders_id_seq'::regclass);


--
-- Name: reviews id; Type: DEFAULT; Schema: public; Owner: vcar
--

ALTER TABLE ONLY public.reviews ALTER COLUMN id SET DEFAULT nextval('public.reviews_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: vcar
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: public; Owner: vcar
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: reviews reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: vcar
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_pkey PRIMARY KEY (id);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: public; Owner: vcar
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (key);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: vcar
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_telegram_id_key; Type: CONSTRAINT; Schema: public; Owner: vcar
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_telegram_id_key UNIQUE (telegram_id);


--
-- Name: reviews reviews_driver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vcar
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_driver_id_fkey FOREIGN KEY (driver_id) REFERENCES public.users(id);


--
-- Name: reviews reviews_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vcar
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: reviews reviews_passenger_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: vcar
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_passenger_id_fkey FOREIGN KEY (passenger_id) REFERENCES public.users(id);


--
-- PostgreSQL database dump complete
--

\unrestrict p71RJfgwdubd34URyMdRMBbqw1gF3YV2hHwvcv8erCTcUmWq0P4bQqbtIUJhVCn

