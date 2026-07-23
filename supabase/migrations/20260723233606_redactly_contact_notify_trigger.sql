-- Email a notification on each new Redactly submission: pg_net posts the row to
-- the redactly-contact-notify Edge Function, which sends via Brevo. Async and
-- non-blocking — a failed notification never blocks or rolls back the insert.
--
-- Namespaced to avoid colliding with the sbrdigital contact form that shares
-- this project. Apply AFTER deploying the Edge Function (it references its URL).

create extension if not exists pg_net with schema extensions;

create or replace function public.notify_redactly_submission()
returns trigger
language plpgsql
security definer
set search_path = public, extensions
as $$
begin
  perform net.http_post(
    url := 'https://pnjsyklmibspekxgslos.supabase.co/functions/v1/redactly-contact-notify',
    headers := jsonb_build_object('Content-Type', 'application/json'),
    body := jsonb_build_object('record', to_jsonb(new))
  );
  return new;
end;
$$;

drop trigger if exists redactly_contact_notify on public.redactly_contact_submissions;
create trigger redactly_contact_notify
  after insert on public.redactly_contact_submissions
  for each row execute function public.notify_redactly_submission();
