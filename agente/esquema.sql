-- Tablas del inventario, sacadas de inventario.dump (pg_restore -s). El agente las crea si la base está vacía.

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

CREATE FUNCTION public.movements_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'movements es inmutable: registra un ajuste en su lugar';

END $$;

SET default_tablespace = '';

SET default_table_access_method = heap;

CREATE TABLE public.bom_lines (
    bom_line_id integer NOT NULL,
    model_id integer NOT NULL,
    part_id integer,
    name text NOT NULL,
    group_name text,
    unit text,
    qty_per_unit integer,
    sort_order integer DEFAULT 0 NOT NULL,
    active boolean DEFAULT true NOT NULL
);

CREATE TABLE public.inspections (
    inspection_id integer NOT NULL,
    work_order_id integer NOT NULL,
    kind text NOT NULL,
    status text NOT NULL,
    approved_by integer,
    approved_at timestamp with time zone,
    reason text,
    items jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT inspections_kind_check CHECK ((kind = ANY (ARRAY['quick'::text, 'full'::text]))),
    CONSTRAINT inspections_status_check CHECK ((status = ANY (ARRAY['submitted'::text, 'approved'::text, 'rejected'::text, 'discarded'::text, 'voided'::text])))
);

CREATE TABLE public.locations (
    location_id integer NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    is_workbench boolean DEFAULT false NOT NULL,
    active boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.models (
    model_id integer NOT NULL,
    model_name text NOT NULL,
    brand text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.movements (
    movement_id bigint NOT NULL,
    part_id integer NOT NULL,
    location_id integer NOT NULL,
    quantity integer NOT NULL,
    type text NOT NULL,
    reference text,
    work_order_id integer,
    purchase_line_id integer,
    unit_cost numeric(14,2),
    currency text,
    issued_by text,
    transfer_id uuid,
    source_event_id uuid,
    occurred_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT movements_currency_check CHECK ((currency = ANY (ARRAY['MXN'::text, 'USD'::text]))),
    CONSTRAINT movements_quantity_check CHECK ((quantity <> 0)),
    CONSTRAINT movements_type_check CHECK ((type = ANY (ARRAY['receipt'::text, 'issue'::text, 'transfer_out'::text, 'transfer_in'::text, 'adjustment'::text])))
);

CREATE SEQUENCE public.movements_movement_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.movements_movement_id_seq OWNED BY public.movements.movement_id;

CREATE TABLE public.needs (
    work_order_id integer NOT NULL,
    bom_line_id integer NOT NULL,
    inspection_id integer NOT NULL,
    inspection_item_id integer NOT NULL,
    part_id integer,
    name text NOT NULL,
    required_qty integer,
    active boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.outbox (
    id bigint NOT NULL,
    event_id uuid NOT NULL,
    event_type text NOT NULL,
    event_key text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    published_at timestamp with time zone
);

CREATE SEQUENCE public.outbox_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.outbox_id_seq OWNED BY public.outbox.id;

CREATE TABLE public.parts (
    part_id integer NOT NULL,
    sku text,
    sku_norm text GENERATED ALWAYS AS (upper(btrim(sku))) STORED,
    name text NOT NULL,
    family text,
    "group" text,
    subgroup text,
    unit_id integer,
    unit_name text,
    image_url text,
    active boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.pending_events (
    id bigint NOT NULL,
    event_id uuid NOT NULL,
    topic text NOT NULL,
    event_type text NOT NULL,
    event_key text NOT NULL,
    payload jsonb NOT NULL,
    waiting_for text,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE public.pending_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.pending_events_id_seq OWNED BY public.pending_events.id;

CREATE TABLE public.processed_events (
    event_id uuid NOT NULL,
    event_type text NOT NULL,
    topic text NOT NULL,
    processed_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE public.reorder_suggestions (
    part_id integer NOT NULL,
    suggested_quantity integer NOT NULL,
    work_order_ids integer[] DEFAULT '{}'::integer[] NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reorder_suggestions_suggested_quantity_check CHECK ((suggested_quantity >= 1))
);

CREATE TABLE public.reservations (
    reservation_id bigint NOT NULL,
    work_order_id integer NOT NULL,
    bom_line_id integer NOT NULL,
    inspection_item_id integer NOT NULL,
    part_id integer NOT NULL,
    quantity integer NOT NULL,
    issued_qty integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reservations_check CHECK ((issued_qty <= quantity)),
    CONSTRAINT reservations_issued_qty_check CHECK ((issued_qty >= 0)),
    CONSTRAINT reservations_quantity_check CHECK ((quantity > 0)),
    CONSTRAINT reservations_status_check CHECK ((status = ANY (ARRAY['active'::text, 'fulfilled'::text, 'cancelled'::text])))
);

CREATE SEQUENCE public.reservations_reservation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.reservations_reservation_id_seq OWNED BY public.reservations.reservation_id;

CREATE TABLE public.shortages (
    shortage_id bigint NOT NULL,
    work_order_id integer NOT NULL,
    bom_line_id integer NOT NULL,
    inspection_item_id integer NOT NULL,
    part_id integer,
    name text NOT NULL,
    missing_quantity integer,
    status text DEFAULT 'open'::text NOT NULL,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    CONSTRAINT shortages_status_check CHECK ((status = ANY (ARRAY['open'::text, 'resolved'::text, 'cancelled'::text])))
);

CREATE SEQUENCE public.shortages_shortage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.shortages_shortage_id_seq OWNED BY public.shortages.shortage_id;

CREATE TABLE public.stock_policies (
    part_id integer NOT NULL,
    min_qty integer DEFAULT 0 NOT NULL,
    max_qty integer,
    reorder_qty integer,
    lead_time_days integer,
    CONSTRAINT stock_policies_check CHECK (((max_qty IS NULL) OR (max_qty >= min_qty))),
    CONSTRAINT stock_policies_lead_time_days_check CHECK (((lead_time_days IS NULL) OR (lead_time_days >= 0))),
    CONSTRAINT stock_policies_min_qty_check CHECK ((min_qty >= 0)),
    CONSTRAINT stock_policies_reorder_qty_check CHECK (((reorder_qty IS NULL) OR (reorder_qty > 0)))
);

CREATE TABLE public.unmatched_receipts (
    id bigint NOT NULL,
    purchase_line_id integer NOT NULL,
    purchase_request_id integer NOT NULL,
    request_number text NOT NULL,
    part_number text NOT NULL,
    description text NOT NULL,
    quantity integer NOT NULL,
    unit_price numeric(14,2),
    currency text,
    work_order_code text,
    received_at timestamp with time zone NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    candidate_part_ids integer[],
    resolved_part_id integer,
    resolved_at timestamp with time zone,
    CONSTRAINT unmatched_receipts_currency_check CHECK ((currency = ANY (ARRAY['MXN'::text, 'USD'::text]))),
    CONSTRAINT unmatched_receipts_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'resolved'::text])))
);

CREATE SEQUENCE public.unmatched_receipts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.unmatched_receipts_id_seq OWNED BY public.unmatched_receipts.id;

CREATE VIEW public.v_stock_by_location AS
 SELECT m.part_id,
    m.location_id,
    l.code,
    (sum(m.quantity))::integer AS on_hand
   FROM (public.movements m
     JOIN public.locations l USING (location_id))
  GROUP BY m.part_id, m.location_id, l.code;

CREATE VIEW public.v_availability AS
 SELECT p.part_id,
    COALESCE(s.on_hand, 0) AS on_hand,
    COALESCE(r.reserved, 0) AS reserved,
    (COALESCE(s.on_hand, 0) - COALESCE(r.reserved, 0)) AS available
   FROM ((public.parts p
     LEFT JOIN ( SELECT v_stock_by_location.part_id,
            (sum(v_stock_by_location.on_hand))::integer AS on_hand
           FROM public.v_stock_by_location
          GROUP BY v_stock_by_location.part_id) s USING (part_id))
     LEFT JOIN ( SELECT reservations.part_id,
            (sum((reservations.quantity - reservations.issued_qty)))::integer AS reserved
           FROM public.reservations
          WHERE (reservations.status = 'active'::text)
          GROUP BY reservations.part_id) r USING (part_id));

CREATE VIEW public.v_ledger AS
 SELECT movement_id,
    part_id,
    location_id,
    quantity,
    type,
    reference,
    work_order_id,
    purchase_line_id,
    unit_cost,
    currency,
    issued_by,
    transfer_id,
    source_event_id,
    occurred_at,
    recorded_at,
    (sum(quantity) OVER (PARTITION BY part_id ORDER BY occurred_at, movement_id))::integer AS balance
   FROM public.movements m;

CREATE TABLE public.work_orders (
    work_order_id integer NOT NULL,
    code text NOT NULL,
    model_id integer,
    department text,
    stage text,
    received_at timestamp with time zone,
    deleted_at timestamp with time zone,
    CONSTRAINT work_orders_department_check CHECK ((department = ANY (ARRAY['spindles'::text, 'motors'::text, 'electronics'::text, 'torque_center'::text])))
);

ALTER TABLE ONLY public.movements ALTER COLUMN movement_id SET DEFAULT nextval('public.movements_movement_id_seq'::regclass);

ALTER TABLE ONLY public.outbox ALTER COLUMN id SET DEFAULT nextval('public.outbox_id_seq'::regclass);

ALTER TABLE ONLY public.pending_events ALTER COLUMN id SET DEFAULT nextval('public.pending_events_id_seq'::regclass);

ALTER TABLE ONLY public.reservations ALTER COLUMN reservation_id SET DEFAULT nextval('public.reservations_reservation_id_seq'::regclass);

ALTER TABLE ONLY public.shortages ALTER COLUMN shortage_id SET DEFAULT nextval('public.shortages_shortage_id_seq'::regclass);

ALTER TABLE ONLY public.unmatched_receipts ALTER COLUMN id SET DEFAULT nextval('public.unmatched_receipts_id_seq'::regclass);

ALTER TABLE ONLY public.bom_lines
    ADD CONSTRAINT bom_lines_pkey PRIMARY KEY (bom_line_id);

ALTER TABLE ONLY public.inspections
    ADD CONSTRAINT inspections_pkey PRIMARY KEY (inspection_id);

ALTER TABLE ONLY public.locations
    ADD CONSTRAINT locations_code_key UNIQUE (code);

ALTER TABLE ONLY public.locations
    ADD CONSTRAINT locations_pkey PRIMARY KEY (location_id);

ALTER TABLE ONLY public.models
    ADD CONSTRAINT models_pkey PRIMARY KEY (model_id);

ALTER TABLE ONLY public.movements
    ADD CONSTRAINT movements_pkey PRIMARY KEY (movement_id);

ALTER TABLE ONLY public.needs
    ADD CONSTRAINT needs_pkey PRIMARY KEY (work_order_id, bom_line_id);

ALTER TABLE ONLY public.outbox
    ADD CONSTRAINT outbox_event_id_key UNIQUE (event_id);

ALTER TABLE ONLY public.outbox
    ADD CONSTRAINT outbox_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.parts
    ADD CONSTRAINT parts_pkey PRIMARY KEY (part_id);

ALTER TABLE ONLY public.pending_events
    ADD CONSTRAINT pending_events_event_id_key UNIQUE (event_id);

ALTER TABLE ONLY public.pending_events
    ADD CONSTRAINT pending_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.processed_events
    ADD CONSTRAINT processed_events_pkey PRIMARY KEY (event_id);

ALTER TABLE ONLY public.reorder_suggestions
    ADD CONSTRAINT reorder_suggestions_pkey PRIMARY KEY (part_id);

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_pkey PRIMARY KEY (reservation_id);

ALTER TABLE ONLY public.shortages
    ADD CONSTRAINT shortages_pkey PRIMARY KEY (shortage_id);

ALTER TABLE ONLY public.stock_policies
    ADD CONSTRAINT stock_policies_pkey PRIMARY KEY (part_id);

ALTER TABLE ONLY public.unmatched_receipts
    ADD CONSTRAINT unmatched_receipts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.unmatched_receipts
    ADD CONSTRAINT unmatched_receipts_purchase_line_id_key UNIQUE (purchase_line_id);

ALTER TABLE ONLY public.work_orders
    ADD CONSTRAINT work_orders_code_key UNIQUE (code);

ALTER TABLE ONLY public.work_orders
    ADD CONSTRAINT work_orders_pkey PRIMARY KEY (work_order_id);

CREATE INDEX bom_lines_model_idx ON public.bom_lines USING btree (model_id);

CREATE INDEX inspections_wo_idx ON public.inspections USING btree (work_order_id);

CREATE UNIQUE INDEX movements_one_receipt_per_line ON public.movements USING btree (purchase_line_id) WHERE (type = 'receipt'::text);

CREATE INDEX movements_part_loc_idx ON public.movements USING btree (part_id, location_id);

CREATE INDEX movements_part_time_idx ON public.movements USING btree (part_id, occurred_at DESC, movement_id DESC);

CREATE INDEX outbox_unpublished_idx ON public.outbox USING btree (id) WHERE (published_at IS NULL);

CREATE INDEX parts_sku_norm_idx ON public.parts USING btree (sku_norm);

CREATE INDEX pending_events_next_idx ON public.pending_events USING btree (next_attempt_at);

CREATE INDEX reservations_active_part_idx ON public.reservations USING btree (part_id) WHERE (status = 'active'::text);

CREATE INDEX reservations_wo_idx ON public.reservations USING btree (work_order_id, bom_line_id);

CREATE UNIQUE INDEX shortages_one_open_per_line ON public.shortages USING btree (work_order_id, bom_line_id) WHERE (status = 'open'::text);

CREATE INDEX shortages_open_part_idx ON public.shortages USING btree (part_id, opened_at) WHERE (status = 'open'::text);

CREATE TRIGGER movements_no_update BEFORE DELETE OR UPDATE ON public.movements FOR EACH ROW EXECUTE FUNCTION public.movements_immutable();

ALTER TABLE ONLY public.bom_lines
    ADD CONSTRAINT bom_lines_model_id_fkey FOREIGN KEY (model_id) REFERENCES public.models(model_id);

ALTER TABLE ONLY public.movements
    ADD CONSTRAINT movements_location_id_fkey FOREIGN KEY (location_id) REFERENCES public.locations(location_id);

ALTER TABLE ONLY public.movements
    ADD CONSTRAINT movements_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(part_id);

ALTER TABLE ONLY public.reorder_suggestions
    ADD CONSTRAINT reorder_suggestions_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(part_id);

ALTER TABLE ONLY public.reservations
    ADD CONSTRAINT reservations_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(part_id);

ALTER TABLE ONLY public.stock_policies
    ADD CONSTRAINT stock_policies_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(part_id);

ALTER TABLE ONLY public.unmatched_receipts
    ADD CONSTRAINT unmatched_receipts_resolved_part_id_fkey FOREIGN KEY (resolved_part_id) REFERENCES public.parts(part_id);

