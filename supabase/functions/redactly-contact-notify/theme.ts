// Brand palette for the Redactly notification email. Email clients strip <link>
// and most <style>, so email HTML must inline every value. These mirror the
// Redactly light-theme design tokens (accent blue #4A9FD4).

export const theme = {
  // Header background + primary text
  ink: "#1F2A37",
  muted: "#5B6572",
  mutedDark: "#2A3542",
  faint: "#9AA4B2",
  onDark: "#9FB0C0",
  white: "#ffffff",

  bg: "#FAFBFC",
  surface2: "#F2F6F9",
  surface3: "#EAF1F8",
  line: "#E5E7EB",

  // Accent — Redactly blue. Used for the eyebrow and the quote bar.
  accent: "#4A9FD4",

  // System font stack (webfonts are unreliable in email).
  font:
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif",
} as const;
