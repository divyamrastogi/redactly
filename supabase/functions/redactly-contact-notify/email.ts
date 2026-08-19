// Branded, email-safe Redactly notification template.
// Rules: table layout, inline styles only, no remote images for critical
// content, always ship a plain-text alternative. Kept separate from index.ts so
// it can be previewed without deploying.

import { theme as t } from "./theme.ts";

const BRAND = "Redactly";

export const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function buildEmail(record: Record<string, string>) {
  const name = esc(record.name);
  const email = esc(record.email);
  const type = esc(record.project_type ?? "");
  const details = esc(record.details).replace(/\r?\n/g, "<br>");
  const subject = `New Redactly enquiry: ${record.name}${
    record.project_type ? ` — ${record.project_type}` : ""
  }`;
  const label =
    `font:500 12px/1.4 ${t.font};letter-spacing:.8px;text-transform:uppercase;color:${t.faint};`;

  const typePill = type
    ? `<div style="display:inline-block;margin-top:12px;padding:5px 12px;background:${t.surface3};border-radius:20px;font:500 13px/1 ${t.font};color:${t.muted};">${type}</div>`
    : "";

  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:${t.bg};">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">${name} — new Redactly enquiry</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:${t.bg};padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:${t.white};border:1px solid ${t.line};border-radius:16px;overflow:hidden;">

        <tr><td style="background:${t.ink};padding:22px 32px;font:600 17px/1 ${t.font};color:${t.white};letter-spacing:-.3px;">${esc(BRAND)}</td></tr>

        <tr><td style="padding:32px 32px 8px;">
          <div style="font:600 12px/1 ${t.font};letter-spacing:1px;text-transform:uppercase;color:${t.accent};padding-bottom:10px;">New enquiry</div>
          <div style="font:600 26px/1.2 ${t.font};color:${t.ink};letter-spacing:-.6px;">${name}</div>
          ${typePill}
        </td></tr>

        <tr><td style="padding:24px 32px 0;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="padding:0 0 4px;${label}">Email</td></tr>
            <tr><td style="padding:0 0 20px;font:400 16px/1.5 ${t.font};color:${t.ink};">
              <a href="mailto:${email}" style="color:${t.ink};text-decoration:underline;">${email}</a>
            </td></tr>
            <tr><td style="padding:0 0 4px;${label}">Message</td></tr>
            <tr><td style="padding:0 0 24px;">
              <div style="background:${t.surface2};border-left:3px solid ${t.accent};border-radius:0 8px 8px 0;padding:16px 18px;font:400 15px/1.6 ${t.font};color:${t.mutedDark};">${details}</div>
            </td></tr>
          </table>
        </td></tr>

        <tr><td style="padding:0 32px 32px;">
          <a href="mailto:${email}" style="display:inline-block;background:${t.accent};color:${t.white};font:500 15px/1 ${t.font};padding:14px 26px;border-radius:10px;text-decoration:none;">Reply to ${name}</a>
        </td></tr>

        <tr><td style="border-top:1px solid ${t.line};padding:20px 32px;background:${t.surface2};font:400 13px/1.5 ${t.font};color:${t.faint};">
          Sent from the Redactly “Add more providers” form. Replying goes straight to ${name}.
        </td></tr>

      </table>
    </td></tr>
  </table>
</body></html>`;

  const text = [
    `NEW REDACTLY ENQUIRY`,
    ``,
    `Name:  ${record.name}`,
    `Email: ${record.email}`,
    record.project_type ? `Type:  ${record.project_type}` : ``,
    ``,
    `Message:`,
    record.details,
    ``,
    `Reply to this email to answer ${record.name} directly.`,
  ].filter((l) => l !== undefined).join("\n");

  return { subject, html, text };
}
