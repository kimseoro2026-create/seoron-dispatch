-- 서로ON 돌봄배정 AI — F0 단계 스키마 (Supabase SQL Editor에서 실행)
-- 열 이름은 data/processed/*.csv와 같다. 실제 개인정보(이름·주소)는 저장하지 않는다.

create extension if not exists postgis;

create table if not exists hospitals (
  hospital_id   text primary key,          -- 심평원 암호화 요양기호(가상 데이터는 MOCK-H...)
  name          text not null,
  class_name    text,
  address       text,
  lat           double precision not null,
  lon           double precision not null,
  geom          geography(point, 4326) generated always as (st_setsrid(st_makepoint(lon, lat), 4326)::geography) stored
);

create table if not exists staff (
  staff_id          text primary key,
  role              text not null check (role in ('escort', 'homecare')),
  skills            text not null,          -- 'escort,homecare' | 'homecare'
  home_lat          double precision not null,
  home_lon          double precision not null,
  shift_start_min   int not null,           -- 자정부터 분 (540 = 09:00)
  shift_end_min     int not null,
  break_start_min   int not null,
  break_minutes     int not null,
  max_work_minutes  int not null
);

create table if not exists clients (
  client_id         text primary key,       -- 가명 ID
  dong              text not null,
  lat               double precision not null,
  lon               double precision not null,
  mobility          text not null check (mobility in ('independent', 'walker', 'wheelchair')),
  joint_escort_ok   boolean not null default false,
  primary_staff_id  text references staff(staff_id),
  geom              geography(point, 4326) generated always as (st_setsrid(st_makepoint(lon, lat), 4326)::geography) stored
);

create table if not exists requests (
  request_id                text primary key,
  service_date              date not null,
  client_id                 text not null references clients(client_id),
  service                   text not null check (service in ('escort', 'homecare')),
  hospital_id               text references hospitals(hospital_id),
  appointment_min           int,
  hospital_stay_min         int,
  est_travel_min            double precision,
  time_window_start_min     int not null,
  time_window_end_min       int not null,
  service_duration_min      int not null,
  discharge_case            boolean not null default false,
  days_since_discharge      int,
  priority                  int not null default 1,
  requested_at              timestamp not null,  -- 기준선(신청순) 정렬 기준
  cancelled_same_day        boolean not null default false,
  cancel_notice_min_before  int,
  status                    text not null default 'received'
                            check (status in ('received', 'assigned', 'done', 'cancelled', 'unassigned')),
  check (time_window_end_min >= time_window_start_min)
);
create index if not exists requests_date_idx on requests (service_date);

create table if not exists nodes (
  node_id  text primary key,               -- AGENCY | HOME:S01 | CLIENT:C0001 | HOSP:...
  kind     text not null,
  lat      double precision not null,
  lon      double precision not null
);

create table if not exists travel_matrix (
  from_node  text not null references nodes(node_id),
  to_node    text not null references nodes(node_id),
  minutes    double precision not null,
  source     text not null check (source in ('osrm', 'estimate')),
  primary key (from_node, to_node)
);

-- F3 이후 단계에서 쓰는 표 (지금은 빈 표로 만들어 둔다)
create table if not exists plan_runs (
  run_id       uuid primary key default gen_random_uuid(),
  kind         text not null check (kind in ('daily', 'reassign', 'simulation')),
  params       jsonb not null default '{}',
  metrics      jsonb,
  code_version text,
  created_at   timestamptz not null default now()
);

create table if not exists assignments (
  run_id      uuid not null references plan_runs(run_id),
  request_id  text not null references requests(request_id),
  staff_id    text not null references staff(staff_id),
  start_min   int not null,
  end_min     int not null,
  seq         int not null,
  approval    text not null default 'proposed' check (approval in ('proposed', 'approved', 'rejected', 'edited')),
  primary key (run_id, request_id)
);

create table if not exists action_logs (
  id          bigserial primary key,
  actor       text not null,
  action      text not null,               -- approve | edit | reject | cancel ...
  target      text,
  detail      jsonb,
  created_at  timestamptz not null default now()
);
