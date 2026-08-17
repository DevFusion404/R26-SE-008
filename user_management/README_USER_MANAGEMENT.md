# User Management Service

A Flask + Supabase backend for user registration, authentication, profile management, and role-based access control.

**Port:** `6000`

---

## Setup

### 1. Navigate to the folder
```powershell
cd 'D:\SLIIT\YEAR 4 SEM 1\IT4010\R26-SE-008\user_management'
```

### 2. Create and activate a virtual environment
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3. Install dependencies
```powershell
pip install -r requirements.txt
```

### 4. Configure environment
Edit `.env` with your Supabase credentials (already set):
```
SUPABASE_URL=https://wxebfbtigzebnddjkmgw.supabase.co
SUPABASE_KEY=sb_publishable_QUxnqlGguhqUzohAS8TWtg_o06huL7T
SECRET_KEY=super-secret-jwt-key-change-in-production
JWT_SECRET=super-secret-jwt-key-change-in-production
PORT=6000
```

### 5. Run the server
```powershell
python app.py
```
The service will start at `http://localhost:6000`.

---

## Supabase `profiles` Table

Run this SQL in your Supabase **SQL Editor** (`https://supabase.com/dashboard/project/<your-project>/sql`):

```sql
-- Enable UUID extension (if not already enabled)
create extension if not exists "uuid-ossp";

-- Create profiles table linked to auth.users
create table public.profiles (
  id          uuid primary key references auth.users(id) on delete cascade,
  email       text not null,
  full_name   text not null,
  role        text not null default 'user' check (role in ('user', 'admin')),
  is_active   boolean not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- Allow authenticated users to read/update their own profile
alter table public.profiles enable row level security;

create policy "Users can view their own profile"
  on public.profiles for select
  using (auth.uid() = id);

create policy "Users can update their own profile"
  on public.profiles for update
  using (auth.uid() = id);

create policy "Admins can view all profiles"
  on public.profiles for select
  using (
    exists (
      select 1 from public.profiles
      where id = auth.uid() and role = 'admin'
    )
  );

create policy "Service role can do everything"
  on public.profiles
  using (true)
  with check (true);

-- Auto-update updated_at on row change
create or replace function public.handle_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger profiles_updated_at
  before update on public.profiles
  for each row execute procedure public.handle_updated_at();
```

---

## API Reference

Base URL: `http://localhost:6000`

### Public Endpoints (no auth required)

| Method | Endpoint              | Description             |
|--------|-----------------------|-------------------------|
| GET    | `/`                   | Root health check       |
| GET    | `/api/auth/health`    | Auth service health     |
| POST   | `/api/auth/register`  | Register a new user     |
| POST   | `/api/auth/login`     | Login and get JWT token |

### Protected Endpoints (Bearer token required)

| Method | Endpoint              | Description                 |
|--------|-----------------------|-----------------------------|
| POST   | `/api/auth/logout`    | Logout current session      |
| GET    | `/api/auth/profile`   | Get own profile             |
| PUT    | `/api/auth/profile`   | Update own profile          |
| DELETE | `/api/auth/account`   | Delete own account          |

### Admin Endpoints (role=admin + Bearer token)

| Method | Endpoint                         | Description         |
|--------|----------------------------------|---------------------|
| GET    | `/api/auth/users`                | List all users      |
| PUT    | `/api/auth/users/<user_id>/role` | Change user role    |

---

## Example curl Commands

### Register
```bash
curl -X POST http://localhost:6000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"john@example.com","password":"Secret123","full_name":"John Doe","role":"user"}'
```

### Login
```bash
curl -X POST http://localhost:6000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"john@example.com","password":"Secret123"}'
```
Save the `access_token` from the response for subsequent requests.

### Get Profile
```bash
curl -X GET http://localhost:6000/api/auth/profile \
  -H "Authorization: Bearer <access_token>"
```

### Update Profile
```bash
curl -X PUT http://localhost:6000/api/auth/profile \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"full_name":"John Updated"}'
```

### Logout
```bash
curl -X POST http://localhost:6000/api/auth/logout \
  -H "Authorization: Bearer <access_token>"
```

### Delete Account
```bash
curl -X DELETE http://localhost:6000/api/auth/account \
  -H "Authorization: Bearer <access_token>"
```

### List All Users (Admin)
```bash
curl -X GET http://localhost:6000/api/auth/users \
  -H "Authorization: Bearer <admin_access_token>"
```

### Change User Role (Admin)
```bash
curl -X PUT http://localhost:6000/api/auth/users/<target_user_id>/role \
  -H "Authorization: Bearer <admin_access_token>" \
  -H "Content-Type: application/json" \
  -d '{"role":"admin"}'
```

---

## Role-Based Access Control

| Role    | Can do                                              |
|---------|-----------------------------------------------------|
| `user`  | Register, login, view/edit own profile, delete own account |
| `admin` | Everything a user can do + list all users, change any user's role |

Roles are stored in the `profiles.role` column. The JWT token carries role metadata from `user_metadata`. On login, the service re-reads the role from the database for accuracy.

---

## Project Structure

```
user_management/
├── app.py                          # Flask app entry point (port 6000)
├── requirements.txt                # Python dependencies
├── .env                            # Environment variables
├── config/
│   ├── __init__.py
│   └── supabase_client.py          # Shared Supabase client instance
├── models/
│   ├── __init__.py
│   └── user_model.py               # Validation helpers
├── middleware/
│   ├── __init__.py
│   └── auth_middleware.py          # JWT decorators: require_auth, require_admin
├── services/
│   ├── __init__.py
│   └── user_service.py             # Business logic (Auth + profiles table)
└── routes/
    ├── __init__.py
    └── user_routes.py              # Flask Blueprint (/api/auth/*)
```

---

## Password Requirements

- Minimum 8 characters
- At least 1 uppercase letter
- At least 1 number

---

## Production Notes

- Replace `JWT_SECRET` / `SECRET_KEY` with a strong random value (e.g. `openssl rand -hex 32`)
- Set `FLASK_ENV=production`
- Run with Gunicorn: `gunicorn -w 4 -b 0.0.0.0:6000 app:app`
- Enable Supabase Row Level Security (RLS) policies (SQL provided above)

---

## Docker Support

This service is containerized for easy deployment and local execution.

### Docker Build & Run (Single Container)

1. **Build the image**:
   ```bash
   docker build -t user-management-service .
   ```

2. **Run the container**:
   Pass your local `.env` configuration file to run on port 6000:
   ```bash
   docker run -p 6000:6000 --env-file .env user-management-service
   ```

### Docker Compose

Alternatively, start the service using Docker Compose:

```bash
docker compose up -d --build
```

The container automatically mounts port `6000` to your host machine and loads settings from the local `.env` file.

