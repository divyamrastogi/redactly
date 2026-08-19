// Emails a notification for each new Redactly contact-form submission.
// Invoked by the redactly_contact_notify trigger (pg_net) on INSERT into
// public.redactly_contact_submissions.
//
// IMPORTANT: this function shares a Supabase project with an unrelated
// sbrdigital contact form. Supabase secrets are PROJECT-WIDE, so every env var
// here is namespaced REDACTLY_* to avoid reading/overwriting sbrdigital's
// CONTACT_NOTIFY_* / BREVO_API_KEY secrets. Transport: Brevo primary, optional
// Resend fallback. Template lives in ./email.ts.

import { buildEmail } from "./email.ts";

const BREVO_API_KEY = Deno.env.get("REDACTLY_BREVO_API_KEY");
const RESEND_API_KEY = Deno.env.get("REDACTLY_RESEND_API_KEY");
const NOTIFY_TO = (Deno.env.get("REDACTLY_NOTIFY_TO") ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);
const FROM = Deno.env.get("REDACTLY_NOTIFY_FROM") ?? "Redactly <onboarding@resend.dev>";

// Optional PDF sample the user shares so we can add support for their bank.
// { name, content } where content is base64. Emailed as an attachment — never
// stored (matches the site's "nothing stored" promise).
type Attachment = { name: string; content: string };

async function sendViaBrevo(record: Record<string, string>, attachment?: Attachment) {
  const { subject, html, text } = buildEmail(record);
  const m = FROM.match(/^(.*)<(.+)>$/); // "Name <email>" -> split for Brevo
  const sender = m ? { name: m[1].trim(), email: m[2].trim() } : { email: FROM };
  const body: Record<string, unknown> = {
    sender,
    to: NOTIFY_TO.map((email) => ({ email })),
    replyTo: { email: record.email },
    subject,
    htmlContent: html,
    textContent: text,
  };
  if (attachment) body.attachment = [{ name: attachment.name, content: attachment.content }];
  const res = await fetch("https://api.brevo.com/v3/smtp/email", {
    method: "POST",
    headers: { "api-key": BREVO_API_KEY!, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`brevo ${res.status}: ${await res.text()}`);
}

async function sendViaResend(record: Record<string, string>, attachment?: Attachment) {
  const { subject, html, text } = buildEmail(record);
  const body: Record<string, unknown> = { from: FROM, to: NOTIFY_TO, reply_to: record.email, subject, html, text };
  if (attachment) body.attachments = [{ filename: attachment.name, content: attachment.content }];
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`resend ${res.status}: ${await res.text()}`);
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("method not allowed", { status: 405 });
  if (NOTIFY_TO.length === 0 || (!BREVO_API_KEY && !RESEND_API_KEY)) {
    return new Response("missing configuration", { status: 500 });
  }

  let record, attachment: Attachment | undefined;
  try {
    ({ record, attachment } = await req.json());
  } catch {
    return new Response("bad request", { status: 400 });
  }
  if (!record?.name || !record?.email || !record?.details) {
    return new Response("bad request", { status: 400 });
  }
  // Reject a malformed attachment rather than silently dropping it.
  if (attachment && (typeof attachment.name !== "string" || typeof attachment.content !== "string")) {
    return new Response("bad request", { status: 400 });
  }

  try {
    if (BREVO_API_KEY) { await sendViaBrevo(record, attachment); return new Response("ok (brevo)", { status: 200 }); }
    await sendViaResend(record, attachment); return new Response("ok (resend)", { status: 200 });
  } catch (err) {
    console.error("send failed", err);
    return new Response("email send failed", { status: 502 });
  }
});
