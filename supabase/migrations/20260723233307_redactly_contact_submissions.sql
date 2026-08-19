-- Redactly "Add more providers" contact form submissions.
--
-- Lives in the SHARED smart-video-controls Supabase project, which also hosts an
-- unrelated sbrdigital contact form on a table literally named
-- `contact_submissions`. To keep the two completely isolated, everything here is
-- namespaced `redactly_` — this migration touches only `redactly_contact_submissions`.
--
-- The RLS policy IS the security model: the public (anon / publishable key) may
-- INSERT and nothing more. No select/update/delete policy exists, so those are
-- denied. Verify empirically after applying — never assume it's correct.

create table if not exists public.redactly_contact_submissions (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  email text not null,
  project_type text not null,
  details text not null,
  created_at timestamptz not null default now()
);

alter table public.redactly_contact_submissions enable row level security;

-- The ONLY policy: anon may insert.
drop policy if exists "anon can insert redactly submissions" on public.redactly_contact_submissions;
create policy "anon can insert redactly submissions"
  on public.redactly_contact_submissions
  for insert
  to anon
  with check (true);
